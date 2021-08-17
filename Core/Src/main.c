#include "main.h"
#include "stock_firmware.h"
#include <inttypes.h>
#include "cmsis_gcc.h"

int foo(){
    return 200;
}

int main(){
    static int a=7;
    return 100;
}

#define BOOTLOADER_MAGIC 0x544F4F42  // "BOOT"
#define BOOTLOADER_MAGIC_ADDRESS ((uint32_t *)0x2001FFF8)
#define BOOTLOADER_JUMP_ADDRESS ((uint32_t **)0x2001FFFC)
static void  __attribute__((naked)) start_app(uint32_t pc, uint32_t sp) {
    __asm("           \n\
          msr msp, r1 /* load r1 into MSP */\n\
          bx r0       /* branch to the address at r0 */\n\
    ");
}
void bootloader(){
    //start_app(0x08017a45, 0x20011330);
    //start_app((uint32_t) stock_Reset_Handler, 0x20011330);

    if(*BOOTLOADER_MAGIC_ADDRESS == BOOTLOADER_MAGIC) {
        *BOOTLOADER_MAGIC_ADDRESS = 0;
        uint32_t sp = (*BOOTLOADER_JUMP_ADDRESS)[0];
        uint32_t pc = (*BOOTLOADER_JUMP_ADDRESS)[1];
        start_app(pc, sp);
    }
    start_app(0x08017a45, 0x20011330);

    //start_app(stock_Reset_Handler, 0x20011330);
    while(1);
}



void Error_Handler(){}
