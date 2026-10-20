/**
 * Mario's game-select custom entries (overview in game_select.c).
 *
 * Custom entries use ids that are not gnw_mode values. Stock code copies the
 * selected id into gnw_mode only after an A-press on an entry that differs
 * from the current mode, so custom ids are handled here before the stock
 * input handler ever sees them.
 */
#include "main.h"
#include "stock_firmware.h"
#include "game_select.h"

#if GAME_SELECT_MENU

#define FB_WIDTH GAME_SELECT_FB_WIDTH

// Stock game-select panel geometry.
#define PANEL_X 87
#define PANEL_Y 31
#define PANEL_W 146
#define PANEL_H 194
#define ICON_X 112
#define ICON_Y 40
#define ICON_PITCH 64
#define ICON_W 96
#define ICON_H 48
#define VISIBLE_ENTRIES 3

#define UI_PALETTE 0xF0
#define CURSOR_TILE 0x3B
#define DIRTY_RECT_GAME_SELECT 3

#define GAME_SELECT_ID_SMB1 0x10
#define GAME_SELECT_ID_SMB2 0x20
#define GAME_SELECT_ID_BALL 0x08
// Custom entry i's id. Not gnw_mode bits; must never reach gnw_mode.
#define GAME_SELECT_ID_CUSTOM(i) (0x0100 << (i))

#define ICON_SIZE (ICON_W * ICON_H / 2)

// Our pointers to the icons (volatile, so reads aren't folded to the NULL
// initializer).
#if GNW_AMALGAM
const uint8_t * const volatile MARIO_GAME_SELECT_SWITCH_ICON = NULL;
static uint8_t switch_icon[ICON_SIZE];
#endif
#if GAME_SELECT_DUAL_BOOT
const uint8_t * const volatile MARIO_GAME_SELECT_DUAL_BOOT_ICON = NULL;
static uint8_t dual_boot_icon[ICON_SIZE];
#endif

static const game_select_custom_t custom[] = {
#if GNW_AMALGAM
    { &MARIO_GAME_SELECT_SWITCH_ICON, switch_icon, game_select_switch },
#endif
#if GAME_SELECT_DUAL_BOOT
    { &MARIO_GAME_SELECT_DUAL_BOOT_ICON, dual_boot_icon, launch_dual_boot },
#endif
};

#define N_CUSTOM (sizeof(custom) / sizeof(custom[0]))

static const struct {
    uint16_t id;
    uint8_t tile;          // Stock iconset tile index.
} stock_entries[] = {
    { GAME_SELECT_ID_SMB1, 0x41 },
    { GAME_SELECT_ID_SMB2, 0x47 },
    { GAME_SELECT_ID_BALL, 0x71 },
};

#define N_STOCK_ENTRIES (sizeof(stock_entries) / sizeof(stock_entries[0]))

static uint8_t n_visible_entries(){
    return N_STOCK_ENTRIES + game_select_n_custom(N_CUSTOM);
}

static uint16_t entry_id(uint8_t i){
    return i < N_STOCK_ENTRIES ? stock_entries[i].id : GAME_SELECT_ID_CUSTOM(i - N_STOCK_ENTRIES);
}

/**
 * Returns the index of `id`; unknown ids (e.g. the menu was opened from the
 * clock) map to the first entry, matching stock behavior.
 */
static uint8_t entry_index(uint16_t id){
    for(uint8_t i = 0; i < n_visible_entries(); i++){
        if(entry_id(i) == id) return i;
    }
    return 0;
}

uint16_t mario_game_select_next(uint16_t choice){
    uint8_t n = n_visible_entries();
    return entry_id((entry_index(choice) + 1) % n);
}

uint16_t mario_game_select_prev(uint16_t choice){
    uint8_t n = n_visible_entries();
    return entry_id((entry_index(choice) + n - 1) % n);
}

void mario_game_select_handle_input(gamepad_t held, gamepad_t pressed){
    uint8_t i = entry_index(*ui_game_select_choice);
    if(pressed == GAMEPAD_A && held == pressed && i >= N_STOCK_ENTRIES){
        custom[i - N_STOCK_ENTRIES].launch();
        // Target invalid; fall back to the current mode so the stock A
        // handler just closes the menu instead of switching to a bogus mode.
        *ui_game_select_choice = *ui_mode_on_open;
    }
    stock_mario_game_select_handle_input(held, pressed);
}

void mario_game_select_draw(uint8_t *fb, ui_rect_t *rects, uint8_t color){
    static uint8_t top = 0;
    static bool icons_ready;

    game_select_inflate_icons(custom, N_CUSTOM, ICON_SIZE, &icons_ready);
    uint8_t n = n_visible_entries();
    uint8_t selected = entry_index(*ui_game_select_choice);
    game_select_scroll(&top, selected, n, VISIBLE_ENTRIES);

    stock_mario_ui_set_dirty_rect(&rects[DIRTY_RECT_GAME_SELECT], PANEL_X, PANEL_Y, PANEL_W, PANEL_H);
    stock_mario_ui_draw_panel(fb + PANEL_Y * FB_WIDTH + PANEL_X, PANEL_W, PANEL_H, color);

    for(uint8_t slot = 0; slot < VISIBLE_ENTRIES && top + slot < n; slot++){
        uint8_t i = top + slot;
        uint8_t *dst = fb + (ICON_Y + slot * ICON_PITCH) * FB_WIDTH + ICON_X;
        if(i >= N_STOCK_ENTRIES){
            game_select_blit_icon(dst, custom[i - N_STOCK_ENTRIES].pixels, ICON_W, ICON_H, UI_PALETTE);
        }
        else{
            stock_mario_ui_blit_4bpp(dst, stock_mario_ui_tile_ptr(stock_entries[i].tile), ICON_W, ICON_H, UI_PALETTE >> 4);
        }
        if(i == selected && !((*ui_blink_counter >> 5) & 1)){
            // Stock cursor position: left of the icon, vertically centered.
            stock_mario_ui_draw_tile16(dst + 16 * FB_WIDTH - 16, stock_mario_ui_tile_ptr(CURSOR_TILE));
        }
    }

    // Scroll hints in the gaps above/below the icons, in the panel border color.
    game_select_scroll_hints(fb, PANEL_X + PANEL_W / 2, ICON_Y, ICON_PITCH, ICON_H, top, n, VISIBLE_ENTRIES, color | 1);
}

#endif
