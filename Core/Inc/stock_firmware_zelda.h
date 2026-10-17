#pragma once

/**
 * Stock functions are absolute symbols: the build turns every function in
 * tools/re/zelda.syms into stock_zelda_<name> (build/stock_symbols.s), so every
 * use is an ELF relocation the patcher can follow when it relocates stock code.
 * Declare a prototype here to call one.
 */
void stock_zelda_Reset_Handler(void);

gamepad_t stock_zelda_read_buttons(void);

#define ZELDA_BACKDROP_COUNT 11
// DTCM .data: pointers to the backdrop images in external flash.
static const uint8_t * const * const stock_backdrop_images = (const uint8_t * const *)0x2000a944;

// ITCM overlay UI (menu code copied from external flash at boot). ITCM is out
// of bl range of internal flash, so these are long calls.
void stock_zelda_ui_draw_text(uint8_t *dst, const uint16_t *tiles, uint32_t n) __attribute__((long_call));
void stock_zelda_ui_draw_cursor(uint8_t *row, uint32_t color) __attribute__((long_call));
uint32_t stock_zelda_ui_cursor_wrap_step(volatile uint8_t *cursor, uint32_t n, gamepad_t pressed) __attribute__((long_call));
uint32_t stock_zelda_game_select_handle_input(gamepad_t held, gamepad_t pressed) __attribute__((long_call));
void stock_zelda_game_select_draw(uint8_t *fb, void *rects, uint32_t color) __attribute__((long_call));

// Overlay UI struct: +0x08 bit 5 is the cursor blink phase, +0x1f the row
// cursor, +0x21/+0x26 nonzero on the SET TIME/other edit pages. Game-select
// menu: +0x2a the row cursor, +0x2c the variant slide direction, and from
// +0x2d 2 bytes per row (chosen and previous variant).
static volatile uint8_t * const stock_ui_state = (volatile uint8_t *)0x2001a7bc;
// menu_rows (5 rows of 11 bytes: [count, variant ids...]) lives in external
// flash, which the patcher rearranges; game_select_handle_input's literal pool
// in ITCM holds its final address.
static const uint8_t * const * const stock_menu_rows_literal = (const uint8_t * const *)0x0000ac60;
// nvram_ram+0x28: ids 8/9 in bits 0/1 and the volume in the upper half; bit 2
// is ours. nvram_set leaves the other bits alone.
static volatile uint32_t * const stock_nvram_flags = (volatile uint32_t *)0x240ff028;
// nvram_set's dirty flag; nvram_commit writes the record when it's set.
static volatile uint8_t * const stock_nvram_dirty = (volatile uint8_t *)0x2001b1ec;
#define ZELDA_NVRAM_SECOND_BEEP_OFF (1 << 2)
