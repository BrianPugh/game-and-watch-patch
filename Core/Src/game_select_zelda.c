/**
 * Zelda's game-select DUAL BOOT row (overview in game_select.c).
 */
#include "main.h"
#include "stock_firmware.h"
#include "game_select.h"

#if GAME_SELECT_DUAL_BOOT

#define FB_WIDTH GAME_SELECT_FB_WIDTH

// Stock game-select layout: 5 rows of 96x32 icons.
#define N_STOCK_ROWS 5
#define VISIBLE_ROWS 5
#define ROW_Y 32
#define ROW_PITCH 40
#define ICON_X 80
#define ICON_W 96
#define ICON_H 32
// menu_rows: [count, variant ids...] per row.
#define MENU_ROW_SIZE 11

#define DUAL_BOOT_ROW N_STOCK_ROWS

#define UI_PALETTE 0xF0
#define ARROW_COLOR (UI_PALETTE | 0xA)  // Yellow, like the variant arrows.

// stock_ui_state offsets.
#define UI_ROW 0x2a
#define UI_VARIANTS 0x2d  // 2 bytes per row.

/**
 * Linear 4bpp (high nibble first) icon; filled in by the patcher.
 *
 * Must not be zero-initialized: the patcher locates free internal flash by
 * searching for a run of zeros, and would place data on top of this array.
 */
const uint8_t ZELDA_GAME_SELECT_DUAL_BOOT_ICON[ICON_W * ICON_H / 2] = { [0 ... ICON_W * ICON_H / 2 - 1] = 0x77 };

/**
 * menu_rows as the stock draw function sees it (the patcher points its
 * literal here): the rows in the window. It only reads each row's count;
 * a count of 0 draws nothing but the cursor.
 */
uint8_t zelda_game_select_view_rows[VISIBLE_ROWS * MENU_ROW_SIZE];

static uint8_t n_rows(){
    // Only offer the dual-boot entry if there's something to boot.
    return game_select_target_valid() ? N_STOCK_ROWS + 1 : N_STOCK_ROWS;
}

/**
 * game_select_handle_input's UP/DOWN step (ui_cursor_wrap_step(&row, 5, pressed)).
 */
uint32_t zelda_game_select_row_step(volatile uint8_t *row, uint32_t n, gamepad_t pressed){
    (void)n;
    return stock_zelda_ui_cursor_wrap_step(row, n_rows(), pressed);
}

/**
 * The DUAL BOOT row has no variants and no app: A boots the target, and
 * the stock handler only sees UP/DOWN/B there (its LEFT/RIGHT and A paths
 * would index past menu_rows and the per-row state).
 */
uint32_t zelda_game_select_handle_input(gamepad_t held, gamepad_t pressed){
    if(stock_ui_state[UI_ROW] == DUAL_BOOT_ROW){
        if(pressed == GAMEPAD_A && held == pressed){
            game_select_launch();
        }
        held &= ~(GAMEPAD_A | GAMEPAD_LEFT | GAMEPAD_RIGHT);
        pressed &= ~(GAMEPAD_A | GAMEPAD_LEFT | GAMEPAD_RIGHT);
    }
    return stock_zelda_game_select_handle_input(held, pressed);
}

/**
 * Stock draw of rows top..top+4: its row i reads the row cursor and the
 * per-row variants as row i, so they're shifted by `top` for the call.
 */
void zelda_game_select_draw(uint8_t *fb, void *rects, uint32_t color){
    static uint8_t top = 0;

    uint8_t n = n_rows();
    uint8_t selected = stock_ui_state[UI_ROW];
    game_select_scroll(&top, selected, n, VISIBLE_ROWS);

    const uint8_t *menu_rows = *stock_menu_rows_literal;
    for(uint8_t i = 0; i < VISIBLE_ROWS; i++){
        uint8_t row = top + i;
        zelda_game_select_view_rows[i * MENU_ROW_SIZE] = row < N_STOCK_ROWS ? menu_rows[row * MENU_ROW_SIZE] : 0;
    }

    volatile uint8_t *variants = &stock_ui_state[UI_VARIANTS];
    uint8_t saved[2 * N_STOCK_ROWS];
    for(uint8_t i = 0; i < sizeof(saved); i++) saved[i] = variants[i];
    for(uint8_t i = 0; i + 2 * top < sizeof(saved); i++) variants[i] = saved[i + 2 * top];
    stock_ui_state[UI_ROW] = selected - top;

    stock_zelda_game_select_draw(fb, rects, color);

    stock_ui_state[UI_ROW] = selected;
    for(uint8_t i = 0; i < sizeof(saved); i++) variants[i] = saved[i];

    if(DUAL_BOOT_ROW < n && DUAL_BOOT_ROW - top < VISIBLE_ROWS){
        uint32_t y = ROW_Y + (DUAL_BOOT_ROW - top) * ROW_PITCH;
        game_select_blit_icon(fb + y * FB_WIDTH + ICON_X, launder(ZELDA_GAME_SELECT_DUAL_BOOT_ICON), ICON_W, ICON_H, UI_PALETTE);
    }

    // Scroll hints between the panel border and the first/last icon.
    game_select_scroll_hints(fb, ICON_X + ICON_W / 2, ROW_Y, ROW_PITCH, ICON_H, top, n, VISIBLE_ROWS, ARROW_COLOR);
}

#endif
