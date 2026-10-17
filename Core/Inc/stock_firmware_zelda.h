#pragma once

/**
 * Stock functions are absolute symbols: the build turns every function in
 * tools/re/<device>.syms into stock_<name> (build/stock_symbols.s), so every
 * use is an ELF relocation the patcher can follow when it relocates stock code.
 * Declare a prototype here to call one.
 */
void stock_Reset_Handler(void);

gamepad_t stock_read_buttons(void);

#define ZELDA_BACKDROP_COUNT 11
// DTCM .data: pointers to the backdrop images in external flash.
static const uint8_t * const * const stock_backdrop_images = (const uint8_t * const *)0x2000a944;

// ITCM overlay UI (menu code copied from external flash at boot). ITCM is out
// of bl range of internal flash, so these are long calls.
void stock_ui_draw_text(uint8_t *dst, const uint16_t *tiles, uint32_t n) __attribute__((long_call));
void stock_ui_draw_cursor(uint8_t *row, uint32_t color) __attribute__((long_call));

// Overlay UI struct: +0x08 bit 5 is the cursor blink phase, +0x1f the row cursor.
static volatile uint8_t * const stock_ui_state = (volatile uint8_t *)0x2001a7bc;
// nvram_ram+0x28: ids 8/9 in bits 0/1 and the volume in the upper half; bit 2
// is ours. nvram_set leaves the other bits alone.
static volatile uint32_t * const stock_nvram_flags = (volatile uint32_t *)0x240ff028;
// nvram_set's dirty flag; nvram_commit writes the record when it's set.
static volatile uint8_t * const stock_nvram_dirty = (volatile uint8_t *)0x2001b1ec;
#define ZELDA_NVRAM_SECOND_BEEP_OFF (1 << 2)
