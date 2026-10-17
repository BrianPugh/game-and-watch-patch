#include "main.h"
#include "stock_firmware.h"
#include <inttypes.h>
#include "cmsis_gcc.h"
#include <assert.h>
#include "gw_linker.h"
#include "stm32h7xx_hal.h"
#include "LzmaDec.h"
#include <string.h>
#include "ips.h"

#define MSP_ADDRESS 0x08000000

#define BANK_1_STACK_2_ADDRESS 0x08020000
#define BANK_2_ADDRESS 0x08100000
#define SD_BOOTLOADER_ADDRESS 0x08032000

// Other software (like retro-go) should set this value
#define BOOTLOADER_MAGIC 0x544F4F42  // "BOOT"

// Intended for internal-use only; bypasses other checks
#define BOOTLOADER_MAGIC_FORCE 0x45435246  // "FRCE"

#if GNW_DEVICE_MARIO
/**
 * This will most likely be overriden by the patcher.
 */
const uint8_t * const SMB1_ROM = (const uint8_t *)0x90001e60;
#endif

#define BOOTLOADER_MAGIC_ADDRESS ((uint32_t *)0x2001FFF8)
#define BOOTLOADER_JUMP_ADDRESS ((uint32_t **)0x2001FFFC)

static void  __attribute__((naked)) start_app(void (* const pc)(void), uint32_t sp) {
    __asm("           \n\
          msr msp, r1 /* load r1 into MSP */\n\
          bx r0       /* branch to the address at r0 */\n\
    ");
}

static inline void set_bootloader(uint32_t address){
    *BOOTLOADER_MAGIC_ADDRESS = BOOTLOADER_MAGIC_FORCE;
    *BOOTLOADER_JUMP_ADDRESS = (uint32_t *)address;
}

/*Light sanity checks on what a good stack-pointer and program counter look like */
static inline bool is_valid(uint32_t pc, uint32_t sp){
    return ((sp >> 24) == 0x20 ) && ((pc >> 24) == 0x08);
}

/**
 * Executed on boot; will jump to a non-default program if:
 *     1. the value at `BOOTLOADER_MAGIC_ADDRESS` is `BOOTLOADER_MAGIC`
 *     2. the value at `BOOTLOADER_JUMP_ADDRESS` is the beginning of
 *        the firmware to execute.
 * So to run that app, set those values and execute a reset.
 */
void bootloader(){
    if(*BOOTLOADER_MAGIC_ADDRESS == BOOTLOADER_MAGIC_FORCE) {
        *BOOTLOADER_MAGIC_ADDRESS = 0;
        uint32_t sp = (*BOOTLOADER_JUMP_ADDRESS)[0];
        uint32_t pc = (*BOOTLOADER_JUMP_ADDRESS)[1];
        if (!is_valid(pc, sp)) goto start_ofw;
        start_app((void (* const)(void)) pc, (uint32_t) sp);
    }

    HAL_Init();

    HAL_PWR_EnableBkUpAccess();
    __HAL_RCC_RTC_ENABLE();
    __HAL_RCC_GPIOC_CLK_ENABLE();

    RTC_HandleTypeDef hrtc = {0};
    hrtc.Instance = RTC;
    // Note: Don't need to call HAL_RTC_Init() since we're just reading backup register

    GPIO_InitTypeDef GPIO_InitStruct = {0};
    GPIO_InitStruct.Pin = BTN_GAME_Pin;
    GPIO_InitStruct.Mode = GPIO_MODE_INPUT;
    GPIO_InitStruct.Pull = GPIO_PULLUP;  // Button connects to GND.
    GPIO_InitStruct.Speed = GPIO_SPEED_FREQ_LOW;

    HAL_GPIO_Init(BTN_GAME_GPIO_Port, &GPIO_InitStruct);

    if(HAL_GPIO_ReadPin(BTN_GAME_GPIO_Port, BTN_GAME_Pin) == GPIO_PIN_RESET) {
        // If GAME is pressed: reset all triggers that might cause us to dual-boot.
        *BOOTLOADER_MAGIC_ADDRESS = 0;
        HAL_RTCEx_BKUPWrite(&hrtc, RTC_BKP_DR0, 0);
    }

    if(*BOOTLOADER_MAGIC_ADDRESS == BOOTLOADER_MAGIC) {
        *BOOTLOADER_MAGIC_ADDRESS = 0;
        uint32_t sp = (*BOOTLOADER_JUMP_ADDRESS)[0];
        uint32_t pc = (*BOOTLOADER_JUMP_ADDRESS)[1];
        if (!is_valid(pc, sp)) goto start_ofw;
        start_app((void (* const)(void)) pc, (uint32_t) sp);
    }


    if(HAL_RTCEx_BKUPRead(&hrtc, RTC_BKP_DR0) == BOOTLOADER_MAGIC){
#if SD_BOOTLOADER
        uint32_t sp = *((uint32_t*)SD_BOOTLOADER_ADDRESS);
        uint32_t pc = *((uint32_t*)SD_BOOTLOADER_ADDRESS + 1);
#else
        uint32_t sp = *((uint32_t*)BANK_2_ADDRESS);
        uint32_t pc = *((uint32_t*)BANK_2_ADDRESS + 1);
#endif

        if (!is_valid(pc, sp)) goto start_ofw;
        start_app((void (* const)(void)) pc, (uint32_t) sp);
    }

start_ofw:
    start_app(stock_Reset_Handler, *(uint32_t *) MSP_ADDRESS);
    while(1);
}


#if ENABLE_SMB1_GRAPHIC_MODS
#define SMB1_GRAPHIC_MODS_MAX 8
const uint8_t * const SMB1_GRAPHIC_MODS[SMB1_GRAPHIC_MODS_MAX] = { 0 };
static volatile uint8_t smb1_graphics_idx = 0;

uint8_t * prepare_clock_rom(void *mario_rom, size_t len){
    const uint8_t *patch = NULL;

    if(smb1_graphics_idx > SMB1_GRAPHIC_MODS_MAX){
        smb1_graphics_idx = 0;
    }
    if(smb1_graphics_idx){
        patch = SMB1_GRAPHIC_MODS[smb1_graphics_idx - 1];
    }

    memcpy(smb1_clock_working, mario_rom, len);

    if(patch) {
        // Load custom graphics
        if(IPS_PATCH_WRONG_HEADER == ips_patch(smb1_clock_working, patch)){
            // Attempt a direct graphics override
            memcpy_inflate(smb1_clock_graphics_working, patch, 0x1ec0);
        }
    }
    else{
        smb1_graphics_idx = 0;
    }

    return stock_prepare_clock_rom(smb1_clock_working, len);
}
#endif

static uint32_t *dual_boot_target(){
#if SD_BOOTLOADER
    return (uint32_t *)SD_BOOTLOADER_ADDRESS;
#else
    return (uint32_t *)BANK_2_ADDRESS;
#endif
}

bool dual_boot_target_valid(){
    uint32_t *target_address = dual_boot_target();
    return is_valid(target_address[1], target_address[0]);
}

void launch_dual_boot(){
    if(dual_boot_target_valid()){
        set_bootloader((uint32_t)dual_boot_target());
        NVIC_SystemReset();
    }
}

#if ENABLE_SMB1_GRAPHIC_MODS
bool is_menu_open(){
    return *ui_draw_status_addr == 5;
}
#endif

gamepad_t read_buttons() {
    static gamepad_t gamepad_last = 0;

    gamepad_t gamepad = 0;
    gamepad = stock_read_buttons();


#if TRIPLE_BOOT
    if((gamepad & GAMEPAD_RIGHT) && (gamepad & GAMEPAD_GAME)){
        set_bootloader(BANK_1_STACK_2_ADDRESS);
        NVIC_SystemReset();
    }
#endif

#if CLOCK_ONLY
    if(gamepad & GAMEPAD_GAME){
#else
    if((gamepad & GAMEPAD_LEFT) && (gamepad & GAMEPAD_GAME)){
#endif
        launch_dual_boot();
    }

#if ENABLE_SMB1_GRAPHIC_MODS
    gnw_mode_t mode = get_gnw_mode();
    if(mode == GNW_MODE_CLOCK && !is_menu_open()){
        // Actions to only perform on the clock screen
        if((gamepad & GAMEPAD_DOWN) && !(gamepad_last &GAMEPAD_DOWN)){
            // TODO: detect if menu is up or not
            smb1_graphics_idx++;
            // Force a reload
            *(uint8_t *)0x2000103d = 1; // Not sure the difference between setting 1 or 2.
        }
    }
#endif

    gamepad_last = gamepad;

    return gamepad;
}


// lc=0, lp=0, pb=2 ((pb * 5 + lp) * 9 + lc = 0x5a), 16 KB dictionary; must
// match lzma_compress() in patches/compression.py.
const uint8_t LZMA_PROP_DATA[5] = {0x5a, 0x00, 0x40, 0x00, 0x00};
// LzmaDecode only allocates the probability model (the output buffer is the
// dictionary): (1984 + 0x300 << (lc + lp)) UInt16s.
#define LZMA_BUF_SIZE            ((1984 + (0x300 << 0)) * 2)

static void *SzAlloc(ISzAllocPtr p, size_t size) {
    void* res = p->Mem;
    return res;
}

static void SzFree(ISzAllocPtr p, void *address) {
}

const ISzAlloc g_Alloc = { SzAlloc, SzFree };

static unsigned char lzma_heap[LZMA_BUF_SIZE];
/**
 * Dropin replacement for memcpy for loading compressed assets.
 * @param n Compressed data length. Can be larger than necessary.
 */
void *memcpy_inflate(uint8_t *dst, const uint8_t *src, size_t n){
    ISzAlloc allocs = {
        .Alloc=SzAlloc,
        .Free=SzFree,
        .Mem=lzma_heap,
    };

    ELzmaStatus status;
    size_t dst_len = 393216;
    LzmaDecode(dst, &dst_len, src, &n, LZMA_PROP_DATA, 5, LZMA_FINISH_ANY, &status, &allocs);
    return dst;
}

#define SLEEP_IMAGE_PIXELS (320 * 240)

/**
 * Replaces the stock GIF decoder for the sleep images (Zelda: backdrops).
 *
 * src is a 256-entry RGB565 palette followed by the LZMA-compressed 8-bit
 * pixel indices. The indices are inflated into the back half of the RGB565
 * dst frame and expanded front-to-back; pixel i's write ends at byte 2i+1,
 * before index i+1 at byte SLEEP_IMAGE_PIXELS+i+1, so no unread index is
 * clobbered.
 *
 * @return 0 (stock decoder's success status).
 */
uint8_t sleep_image_inflate(const uint8_t *src, uint16_t *dst){
    const uint16_t *palette = (const uint16_t *)src;
    uint8_t *indices = (uint8_t *)dst + SLEEP_IMAGE_PIXELS;

    memcpy_inflate(indices, src + 256 * sizeof(uint16_t), SLEEP_IMAGE_PIXELS);
    for(uint32_t i = 0; i < SLEEP_IMAGE_PIXELS; i++){
        dst[i] = palette[indices[i]];
    }
    return 0;
}

#if GNW_DEVICE_ZELDA
/**
 * Replaces backdrop_tick's tail call to the stock decode-by-index helper
 * (0x08019f36), which ignores an index past the last backdrop.
 */
uint8_t backdrop_inflate(uint16_t *dst, int32_t index){
    if(index >= ZELDA_BACKDROP_COUNT){
        return 0;
    }
    return sleep_image_inflate(stock_backdrop_images[index], dst);
}

/**
 * LoZ2 EN's CHR ROM, read in place from external flash; the patcher relocates
 * this pointer when the external data moves.
 */
const uint8_t * const LOZ2_CHR = (const uint8_t *)0x90090000;

/**
 * Replaces loz2_load's "CHR = PRG ROM + 0x20000" (adds r0, r5, r4;
 * str.w r0, [r6, #0xf4c]): the PRG ROM is stored compressed now, so the CHR
 * no longer follows it. r6 is the NES context.
 */
void __attribute__((naked)) loz2_set_chr(void){
    __asm("                         \n\
          ldr r0, =LOZ2_CHR         \n\
          ldr r0, [r0]              \n\
          str.w r0, [r6, #0xf4c]    \n\
          bx lr                     \n\
          .ltorg                    \n\
    ");
}

/*
 * The clock settings overlay gets a 5th row, SECOND BEEP ON/OFF. The setting
 * is bit 2 of nvram word +0x28 (saved with the other settings); the patched
 * clock_tick reads it before queuing the beep.
 */
#define T(c) (0xa240 + 2 * ((c) - 'A') + ((c) >= 'Q' ? 0x40 - 2 * 16 : 0))
#define ICON_SPEAKER 0xa05e
#define SPACE 0x9442
static const uint16_t SECOND_BEEP_ON[] = {
    ICON_SPEAKER, SPACE, T('S'), T('E'), T('C'), T('O'), T('N'), T('D'), SPACE,
    T('B'), T('E'), T('E'), T('P'), SPACE, T('O'), T('N'),
};
static const uint16_t SECOND_BEEP_OFF[] = {
    ICON_SPEAKER, SPACE, T('S'), T('E'), T('C'), T('O'), T('N'), T('D'), SPACE,
    T('B'), T('E'), T('E'), T('P'), SPACE, T('O'), T('F'), T('F'),
};
#undef T

// Bytes between the overlay's rows (24 lines of the 320-byte-wide 8-bit
// frame); the patcher sets the same spacing in settings_clock_draw.
#define SETTINGS_ROW_STEP (24 * 320)
#define SETTINGS_SECOND_BEEP_ROW 4

/**
 * Row 4's input; settings_clock_handle_input returns this (nonzero closes
 * the overlay and saves). A, LEFT and RIGHT toggle the setting; B closes.
 */
uint32_t second_beep_input(gamepad_t held, gamepad_t pressed){
    if(pressed & (GAMEPAD_A | GAMEPAD_LEFT | GAMEPAD_RIGHT)){
        *stock_nvram_flags ^= ZELDA_NVRAM_SECOND_BEEP_OFF;
        *stock_nvram_dirty = 1;
    }
    return (pressed & GAMEPAD_B) != 0;
}

/**
 * settings_clock_handle_input's path for a row past 3 (0x0000a63e) branches
 * here through an ITCM veneer, inside its push {r4, r5, r6, lr}: r4 holds the
 * pressed buttons, r6 the held ones.
 */
void __attribute__((naked)) settings_row_input(void){
    __asm("                         \n\
          mov r0, r6                \n\
          mov r1, r4                \n\
          pop {r4, r5, r6, lr}      \n\
          b second_beep_input       \n\
    ");
}

/**
 * Draws the AUTO SLEEP text (the stock tail call) and then the 5th row.
 */
void second_beep_draw(uint8_t *dst, const uint16_t *tiles, uint32_t n){
    stock_ui_draw_text(dst, tiles, n);
    uint8_t *row = dst - 16 + SETTINGS_ROW_STEP;  // The text starts 16 px in.
    if(stock_ui_state[0x1f] == SETTINGS_SECOND_BEEP_ROW){
        stock_ui_draw_cursor(row, (stock_ui_state[0x08] & 0x20) ? 0 : 0x90d8);
    }
    if(*stock_nvram_flags & ZELDA_NVRAM_SECOND_BEEP_OFF){
        stock_ui_draw_text(row + 16, SECOND_BEEP_OFF, sizeof(SECOND_BEEP_OFF) / 2);
    }
    else{
        stock_ui_draw_text(row + 16, SECOND_BEEP_ON, sizeof(SECOND_BEEP_ON) / 2);
    }
}

/**
 * settings_clock_draw's last tail call (0x0000afba: add sp, #12, then pop
 * {r4-r11, lr} and b ui_draw_text) branches here through an ITCM veneer
 * instead; r0-r2 are the AUTO SLEEP text's arguments.
 */
void __attribute__((naked)) settings_draw_tail(void){
    __asm("                         \n\
          add sp, #12               \n\
          pop {r4-r11, lr}          \n\
          b second_beep_draw        \n\
    ");
}
#endif

/**
 * This gets hooked into the rwdata/bss init table.
 */
int32_t *rwdata_inflate(int32_t *table){
    uint8_t *data = (uint8_t *)table + table[0];
    int32_t len = table[1];
    uint8_t *ram = (uint8_t *) table[2];
    memcpy_inflate(ram, data, len);
    return table + 3;
}


/**
 * This gets hooked into the rwdata/bss init table.
 */
int32_t *bss_rwdata_init(int32_t *table){
    /* Copy init values from text to data */
    uint32_t *init_values_ptr = &_sidata;
    uint32_t *data_ptr = &_sdata;

    if (init_values_ptr != data_ptr) {
        for (; data_ptr < &_edata;) {
            *data_ptr++ = *init_values_ptr++;
        }
    }

    /* Clear the zero segment */
    for (uint32_t *bss_ptr = &_sbss; bss_ptr < &_ebss;) {
        *bss_ptr++ = 0;
    }
    return table;
}


#if ENABLE_SMB1_GRAPHIC_MODS
gnw_mode_t get_gnw_mode(){
    uint8_t val = *gnw_mode_addr;
    if(val == 0x20) return GNW_MODE_SMB2;
    else if(val == 0x10) return GNW_MODE_SMB1;
    else if(val == 0x08) return GNW_MODE_BALL;
    else return GNW_MODE_CLOCK;
}
#endif

void NMI_Handler(void) {
    __BKPT(0);
}

void HardFault_Handler(void) {
    __BKPT(0);
}
