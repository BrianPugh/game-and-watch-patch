#pragma once

/**
 * Stock functions are absolute symbols: the build turns every function in
 * tools/re/mario.syms into stock_mario_<name> (build/stock_symbols.s), so every
 * use is an ELF relocation the patcher can follow when it relocates stock code.
 * Declare a prototype here to call one.
 */
void stock_mario_Reset_Handler(void);

gamepad_t stock_mario_read_buttons(void);


/**
 * Returns `true` if USB power is connected, `false` otherwise.
 */
bool stock_mario_is_usb_connected(void);

/**
 * Put system to sleep.
 */
void stock_mario_sleep(void);

/**
 * Address for checking to see if we are in {Clock, BALL, SMB1, SMB2}.
 * See `get_gnw_mode()`
 */
static volatile uint8_t * const gnw_mode_addr = 0x20001044;

/**
 * Defined in mario.c (the patcher looks it up by name and overrides it).
 */
extern const uint8_t * const SMB1_ROM;

#define SMB1_CLOCK_WORKING 0x24000000
static uint8_t * const smb1_clock_working = SMB1_CLOCK_WORKING;
static uint8_t * const smb1_clock_graphics_working = SMB1_CLOCK_WORKING + 0x8000;


static volatile uint8_t * const ui_draw_status_addr = 0x20010694;

/**
 * Function that loads the SMB1 rom into memory and prepares all the sprite
 * data.
 */
uint8_t *stock_mario_prepare_clock_rom(uint8_t *src, size_t len);

/*******************************************************************************
 * Game-select / settings overlay UI.
 * See tools/re/mario.syms for the full map of this subsystem.
 ******************************************************************************/

/**
 * A dirty rectangle in the overlay renderer's rect array.
 */
typedef struct {
    uint16_t x, y, w, h;
} ui_rect_t;

/**
 * Value under the game-select cursor. Stock values are gnw_mode values:
 * 0x10 (SMB1), 0x20 (SMB2), 0x08 (BALL).
 */
static volatile uint16_t * const ui_game_select_choice = 0x20010698;

/**
 * gnw_mode at the time the overlay was opened.
 */
static volatile uint16_t * const ui_mode_on_open = 0x20010696;

/**
 * Bit 5 toggles the cursor blink; reset to 0 whenever the cursor moves.
 */
static volatile uint8_t * const ui_blink_counter = 0x200106a0;

/**
 * Handles a button press while the game-select overlay is open.
 * Only acts when exactly one button is newly pressed (held == pressed).
 */
void stock_mario_game_select_handle_input(gamepad_t held, gamepad_t pressed);

/**
 * Returns a pointer to a 16x16 4bpp UI tile.
 * idx < 0x30: RAM font glyph; else iconset tile (idx - 0x30).
 */
const uint8_t *stock_mario_ui_tile_ptr(uint8_t idx);

/**
 * Draws an opaque 16x16 UI tile (palette 0xF0-0xFF) into the 320-wide framebuffer.
 */
void stock_mario_ui_draw_tile16(uint8_t *dst, const uint8_t *tile);

/**
 * Opaque blit of a w*h region of the 256px-wide 4bpp iconset into the framebuffer.
 * Each pixel is written as (nibble | pal << 4).
 */
void stock_mario_ui_blit_4bpp(uint8_t *dst, const uint8_t *src, uint32_t w, uint32_t h, uint8_t pal);

void stock_mario_ui_set_dirty_rect(ui_rect_t *rect, uint16_t x, uint16_t y, uint16_t w, uint16_t h);

/**
 * Filled rectangle: interior is `color`, 1px border is `color | 1`.
 */
void stock_mario_ui_draw_panel(uint8_t *dst, uint32_t w, uint32_t h, uint8_t color);
