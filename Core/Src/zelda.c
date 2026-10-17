/**
 * Zelda-only code. In the amalgam build, the RAM of every *zelda.o lives in
 * Zelda's free RAM (STM32H7B0VBTx_FLASH_amalgam.ld).
 */
#include "main.h"
#include "stock_firmware.h"

uint8_t zelda_lzma_heap[LZMA_BUF_SIZE];

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
 * Draws the stock tail call's text (AUTO SLEEP on the main page) and then the
 * 5th row.
 */
void second_beep_draw(uint8_t *dst, const uint16_t *tiles, uint32_t n){
    stock_zelda_ui_draw_text(dst, tiles, n);
    // The SET TIME (+0x21) and other edit (+0x26) pages end in the same tail
    // call with their own layout; the rows are only on the main page.
    if(stock_ui_state[0x21] || stock_ui_state[0x26]){
        return;
    }
    uint8_t *row = dst - 16 + SETTINGS_ROW_STEP;  // The text starts 16 px in.
    if(stock_ui_state[0x1f] == SETTINGS_SECOND_BEEP_ROW){
        stock_zelda_ui_draw_cursor(row, (stock_ui_state[0x08] & 0x20) ? 0 : 0x90d8);
    }
    if(*stock_nvram_flags & ZELDA_NVRAM_SECOND_BEEP_OFF){
        stock_zelda_ui_draw_text(row + 16, SECOND_BEEP_OFF, sizeof(SECOND_BEEP_OFF) / 2);
    }
    else{
        stock_zelda_ui_draw_text(row + 16, SECOND_BEEP_ON, sizeof(SECOND_BEEP_ON) / 2);
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
