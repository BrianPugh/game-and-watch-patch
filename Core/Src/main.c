#include "main.h"
#include "stock_firmware.h"
#include <inttypes.h>
#include "cmsis_gcc.h"
#include <assert.h>
#include "gw_linker.h"
#include "stm32h7xx_hal.h"


#define BANK_2_ADDRESS 0x08100000
#define BOOTLOADER_MAGIC 0x544F4F42  // "BOOT"
#define BOOTLOADER_MAGIC_ADDRESS ((uint32_t *)0x2001FFF8)
#define BOOTLOADER_JUMP_ADDRESS ((uint32_t **)0x2001FFFC)
static void  __attribute__((naked)) start_app(uint32_t pc, uint32_t sp) {
    __asm("           \n\
          msr msp, r1 /* load r1 into MSP */\n\
          bx r0       /* branch to the address at r0 */\n\
    ");
}

static inline void set_bootloader(uint32_t address){
    *BOOTLOADER_MAGIC_ADDRESS = BOOTLOADER_MAGIC;
    *BOOTLOADER_JUMP_ADDRESS = address;
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
        start_app((uint32_t) pc, (uint32_t) sp);
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
    if((gamepad & GAMEPAD_LEFT) && (gamepad & GAMEPAD_A) && (gamepad & GAMEPAD_GAME)){
        start_bank_2();
    }
    return gamepad;
}



void Error_Handler(){}
