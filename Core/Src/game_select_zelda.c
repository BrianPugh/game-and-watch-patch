/**
 * Zelda's game-select custom rows (overview in game_select.c).
 */
#include "main.h"
#include "stock_firmware.h"
#include "game_select.h"

#if GAME_SELECT_MENU

#define FB_WIDTH GAME_SELECT_FB_WIDTH

// Stock game-select layout: 5 rows of 96x32 icons.
#define MAX_STOCK_ROWS 5
#define VISIBLE_ROWS 5
#define ROW_Y 32
#define ROW_PITCH 40
#define ICON_X 80
#define ICON_W 96
#define ICON_H 32
// menu_rows: [count, variant ids...] per row.
#define MENU_ROW_SIZE 11

#define UI_PALETTE 0xF0
#define ARROW_COLOR (UI_PALETTE | 0xA)  // Yellow, like the variant arrows.

// stock_ui_state offsets.
#define UI_ROW 0x2a
#define UI_VARIANTS 0x2d  // 2 bytes per row.

#define ICON_SIZE (ICON_W * ICON_H / 2)

// Our pointers to the icons (volatile, so reads aren't folded to the NULL
// initializer).
#if GNW_AMALGAM
const uint8_t * const volatile ZELDA_GAME_SELECT_SWITCH_ICON = NULL;
static uint8_t switch_icon[ICON_SIZE];
#endif
#if GAME_SELECT_DUAL_BOOT
const uint8_t * const volatile ZELDA_GAME_SELECT_DUAL_BOOT_ICON = NULL;
static uint8_t dual_boot_icon[ICON_SIZE];
#endif

static const game_select_custom_t custom[] = {
#if GNW_AMALGAM
    { &ZELDA_GAME_SELECT_SWITCH_ICON, switch_icon, game_select_switch },
#endif
#if GAME_SELECT_DUAL_BOOT
    { &ZELDA_GAME_SELECT_DUAL_BOOT_ICON, dual_boot_icon, launch_dual_boot },
#endif
};

#define N_CUSTOM (sizeof(custom) / sizeof(custom[0]))

/**
 * menu_rows as the stock draw function sees it (the patcher points its
 * literal here): the rows in the window. It only reads each row's count;
 * a count of 0 draws nothing but the cursor.
 */
uint8_t zelda_game_select_view_rows[VISIBLE_ROWS * MENU_ROW_SIZE];

/**
 * The stock rows in use: the patcher may drop some (--no-la) and moves the
 * rest up, leaving a count of 0 in the unused rows. The custom rows follow
 * them.
 */
static uint8_t n_stock_rows(){
    const uint8_t *menu_rows = *stock_menu_rows_literal;
    uint8_t n = 0;
    while(n < MAX_STOCK_ROWS && menu_rows[n * MENU_ROW_SIZE]) n++;
    return n;
}

static uint8_t n_rows(){
    return n_stock_rows() + game_select_n_custom(N_CUSTOM);
}

/**
 * game_select_handle_input's UP/DOWN step (ui_cursor_wrap_step(&row, 5, pressed)).
 */
uint32_t zelda_game_select_row_step(volatile uint8_t *row, uint32_t n, gamepad_t pressed){
    (void)n;
    return stock_zelda_ui_cursor_wrap_step(row, n_rows(), pressed);
}

/**
 * The custom rows have no variants and no app: A boots the target, and the
 * stock handler only sees UP/DOWN/B there (its LEFT/RIGHT and A paths would
 * act on an empty stock row, or index past menu_rows and the per-row state).
 */
uint32_t zelda_game_select_handle_input(gamepad_t held, gamepad_t pressed){
    uint8_t n_stock = n_stock_rows();
    uint8_t row = stock_ui_state[UI_ROW];
    if(row >= n_stock){
        if(pressed == GAMEPAD_A && held == pressed){
            custom[row - n_stock].launch();
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
    static bool icons_ready;

    game_select_inflate_icons(custom, N_CUSTOM, ICON_SIZE, &icons_ready);
    uint8_t n_stock = n_stock_rows();
    uint8_t n = n_rows();
    uint8_t selected = stock_ui_state[UI_ROW];
    game_select_scroll(&top, selected, n, VISIBLE_ROWS);

    const uint8_t *menu_rows = *stock_menu_rows_literal;
    for(uint8_t i = 0; i < VISIBLE_ROWS; i++){
        uint8_t row = top + i;
        zelda_game_select_view_rows[i * MENU_ROW_SIZE] = row < n_stock ? menu_rows[row * MENU_ROW_SIZE] : 0;
    }

    volatile uint8_t *variants = &stock_ui_state[UI_VARIANTS];
    uint8_t saved[2 * MAX_STOCK_ROWS];
    for(uint8_t i = 0; i < sizeof(saved); i++) saved[i] = variants[i];
    for(uint8_t i = 0; i + 2 * top < sizeof(saved); i++) variants[i] = saved[i + 2 * top];
    stock_ui_state[UI_ROW] = selected - top;

    stock_zelda_game_select_draw(fb, rects, color);

    stock_ui_state[UI_ROW] = selected;
    for(uint8_t i = 0; i < sizeof(saved); i++) variants[i] = saved[i];

    for(uint8_t row = n_stock > top ? n_stock : top; row < n && row < top + VISIBLE_ROWS; row++){
        uint32_t y = ROW_Y + (row - top) * ROW_PITCH;
        game_select_blit_icon(fb + y * FB_WIDTH + ICON_X, custom[row - n_stock].pixels, ICON_W, ICON_H, UI_PALETTE);
    }

    // Scroll hints between the panel border and the first/last icon.
    game_select_scroll_hints(fb, ICON_X + ICON_W / 2, ROW_Y, ROW_PITCH, ICON_H, top, n, VISIBLE_ROWS, ARROW_COLOR);
}

#endif
