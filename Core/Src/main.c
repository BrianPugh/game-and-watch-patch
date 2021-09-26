#include "main.h"
#include "stock_firmware.h"
#include <inttypes.h>
#include "cmsis_gcc.h"
#include <assert.h>
#include "gw_linker.h"
#include "stm32h7xx_hal.h"
#include "LzmaDec.h"


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
    /* Copy init values from text to data */
    uint32_t *init_values_ptr = &_sidata;
    uint32_t *data_ptr = &_sdata;

    /* Initialize non-constant static variable with initial values */
    if (init_values_ptr != data_ptr) {
        for (; data_ptr < &_edata;) {
            *data_ptr++ = *init_values_ptr++;
        }
    }

    /* Clear the zero segment */
    for (uint32_t *bss_ptr = &_sbss; bss_ptr < &_ebss;) {
        *bss_ptr++ = 0;
    }

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

gamepad_t read_buttons() {
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

    if(mode == GNW_MODE_CLOCK){
        // Actions to only perform on the clock screen
    }

    return gamepad;
}

#define LZMA_BUF_SIZE            (1<<15)

static void *SzAlloc(ISzAllocPtr p, size_t size) {
    void* res = p->Mem;
    //p->Mem += size;
    return res;
}

static void SzFree(ISzAllocPtr p, void *address) {
}

const ISzAlloc g_Alloc = { SzAlloc, SzFree };

/**
 * Dropin replacement for memcpy for loading compressed assets.
 * @param n Compressed data length
 */
void *memcpy_inflate(uint8_t *dst, uint8_t *src, size_t n){
    unsigned char lzma_heap[LZMA_BUF_SIZE];
    ISzAlloc allocs = {
        .Alloc=SzAlloc,
        .Free=SzFree,
        .Mem=lzma_heap,
    };

    ELzmaStatus lzmaStatus;
    n -= 13;
    size_t dst_len = 393216;
    LzmaDecode(dst, &dst_len, &src[13], &n, src, 5, LZMA_FINISH_ANY, &lzmaStatus, &allocs);
    return dst;
}

int32_t *rwdata_inflate(int32_t *table){
    uint8_t *data = (uint8_t *)table + table[0];
    int32_t len = table[1];
    uint8_t *ram = (uint8_t *) table[2];
    memcpy_inflate(ram, data, len);
    return table + 3;
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
