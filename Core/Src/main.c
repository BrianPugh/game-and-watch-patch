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


#define BANK_2_ADDRESS 0x08100000
#define BOOTLOADER_MAGIC 0x544F4F42  // "BOOT"
#define BOOTLOADER_MAGIC_ADDRESS ((uint32_t *)0x2001FFF8)
#define BOOTLOADER_JUMP_ADDRESS ((uint32_t **)0x2001FFFC)
static void  __attribute__((naked)) start_app(void (* const pc)(void), uint32_t sp) {
    __asm("           \n\
          msr msp, r1 /* load r1 into MSP */\n\
          bx r0       /* branch to the address at r0 */\n\
    ");
}

static inline void set_bootloader(uint32_t address){
    *BOOTLOADER_MAGIC_ADDRESS = BOOTLOADER_MAGIC;
    *BOOTLOADER_JUMP_ADDRESS = (uint32_t *)address;
}

/**
 * Executed on boot; will jump to a non-default program if:
 *     1. the value at `BOOTLOADER_MAGIC_ADDRESS` is `BOOTLOADER_MAGIC`
 *     2. the value at `BOOTLOADER_JUMP_ADDRESS` is the beginning of
 *        the firmware to execute.
 * So to run that app, set those values and execute a reset.
 */
void bootloader(){
    if(*BOOTLOADER_MAGIC_ADDRESS == BOOTLOADER_MAGIC) {
        *BOOTLOADER_MAGIC_ADDRESS = 0;
        uint32_t sp = (*BOOTLOADER_JUMP_ADDRESS)[0];
        uint32_t pc = (*BOOTLOADER_JUMP_ADDRESS)[1];
        start_app((void (* const)(void)) pc, (uint32_t) sp);
    }

    start_app(stock_Reset_Handler, 0x20011330);
    while(1);
}

static inline void start_bank_2() {
    set_bootloader(BANK_2_ADDRESS);
    NVIC_SystemReset();
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
    if(patch) {
        // Load custom graphics
        if(IPS_PATCH_WRONG_HEADER == ips_patch(smb1_clock_working, mario_rom, patch)){
            // Attempt a direct graphics override
            memcpy(smb1_clock_working, mario_rom, len);
            memcpy_inflate(smb1_clock_graphics_working, patch, 0x1ec0);
        }
    }
    else{
        memcpy(smb1_clock_working, mario_rom, len);
        smb1_graphics_idx = 0;
    }

    return stock_prepare_clock_rom(smb1_clock_working, len);
}
#endif

bool is_menu_open(){
    return *ui_draw_status_addr == 5;
}

gamepad_t read_buttons() {
    static gamepad_t gamepad_last = 0;

    gamepad_t gamepad = 0;
    gamepad = stock_read_buttons();

    gnw_mode_t mode = get_gnw_mode();

#if CLOCK_ONLY
    if(gamepad & GAMEPAD_GAME){
#else
    if((gamepad & GAMEPAD_LEFT) && (gamepad & GAMEPAD_GAME)){
#endif
        start_bank_2();
    }

    if(mode == GNW_MODE_CLOCK && !is_menu_open()){
#if ENABLE_SMB1_GRAPHIC_MODS
        // Actions to only perform on the clock screen
        if((gamepad & GAMEPAD_DOWN) && !(gamepad_last &GAMEPAD_DOWN)){
            // TODO: detect if menu is up or not
            smb1_graphics_idx++;
            // Force a reload
            *(uint8_t *)0x2000103d = 1; // Not sure the difference between setting 1 or 2.
        }
#endif
    }

    gamepad_last = gamepad;

    return gamepad;
}


const uint8_t LZMA_PROP_DATA[5] = {0x5d, 0x00, 0x40, 0x00, 0x00};
#define LZMA_BUF_SIZE            16256

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


gnw_mode_t get_gnw_mode(){
    uint8_t val = *gnw_mode_addr;
    if(val == 0x20) return GNW_MODE_SMB2;
    else if(val == 0x10) return GNW_MODE_SMB1;
    else if(val == 0x08) return GNW_MODE_BALL;
    else return GNW_MODE_CLOCK;
}


void NMI_Handler(void) {
    __BKPT(0);
}

void HardFault_Handler(void) {
    __BKPT(0);
}
