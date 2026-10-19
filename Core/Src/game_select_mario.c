/**
 * Mario's game-select DUAL BOOT entry (overview in game_select.c).
 *
 * Custom entries use ids that are not gnw_mode values. Stock code copies the
 * selected id into gnw_mode only after an A-press on an entry that differs
 * from the current mode, so custom ids are handled here before the stock
 * input handler ever sees them.
 */
#include "main.h"
#include "stock_firmware.h"
#include "game_select.h"

#if GAME_SELECT_DUAL_BOOT

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
// Not a gnw_mode bit; must never reach gnw_mode.
#define GAME_SELECT_ID_DUAL_BOOT 0x0100

/**
 * Linear 4bpp (high nibble first) icon, wherever the patcher put it: internal
 * flash if it fits, else external flash. volatile, so reads aren't folded to
 * the NULL initializer.
 */
const uint8_t * const volatile MARIO_GAME_SELECT_DUAL_BOOT_ICON = NULL;

typedef struct {
    uint16_t id;
    uint8_t tile;          // Stock iconset tile index, used if `icon` is NULL.
    // Pointer to a linear 4bpp ICON_W x ICON_H image the patcher places.
    const uint8_t * const volatile *icon;
} game_select_entry_t;

static const game_select_entry_t entries[] = {
    { GAME_SELECT_ID_SMB1, 0x41, NULL },
    { GAME_SELECT_ID_SMB2, 0x47, NULL },
    { GAME_SELECT_ID_BALL, 0x71, NULL },
    { GAME_SELECT_ID_DUAL_BOOT, 0, &MARIO_GAME_SELECT_DUAL_BOOT_ICON },
};

#define N_STOCK_ENTRIES 3
#define N_ENTRIES (sizeof(entries) / sizeof(entries[0]))

static uint8_t n_visible_entries(){
    // Only offer the dual-boot entry if there's something to boot.
    return game_select_target_valid() ? N_ENTRIES : N_STOCK_ENTRIES;
}

/**
 * Returns the index of `id`; unknown ids (e.g. the menu was opened from the
 * clock) map to the first entry, matching stock behavior.
 */
static uint8_t entry_index(uint16_t id){
    for(uint8_t i = 0; i < n_visible_entries(); i++){
        if(entries[i].id == id) return i;
    }
    return 0;
}

uint16_t mario_game_select_next(uint16_t choice){
    uint8_t n = n_visible_entries();
    return entries[(entry_index(choice) + 1) % n].id;
}

uint16_t mario_game_select_prev(uint16_t choice){
    uint8_t n = n_visible_entries();
    return entries[(entry_index(choice) + n - 1) % n].id;
}

void mario_game_select_handle_input(gamepad_t held, gamepad_t pressed){
    if(pressed == GAMEPAD_A && held == pressed && *ui_game_select_choice == GAME_SELECT_ID_DUAL_BOOT){
        game_select_launch();
        // Target invalid; fall back to the current mode so the stock A
        // handler just closes the menu instead of switching to a bogus mode.
        *ui_game_select_choice = *ui_mode_on_open;
    }
    stock_mario_game_select_handle_input(held, pressed);
}

void mario_game_select_draw(uint8_t *fb, ui_rect_t *rects, uint8_t color){
    static uint8_t top = 0;

    uint8_t n = n_visible_entries();
    uint8_t selected = entry_index(*ui_game_select_choice);
    game_select_scroll(&top, selected, n, VISIBLE_ENTRIES);

    stock_mario_ui_set_dirty_rect(&rects[DIRTY_RECT_GAME_SELECT], PANEL_X, PANEL_Y, PANEL_W, PANEL_H);
    stock_mario_ui_draw_panel(fb + PANEL_Y * FB_WIDTH + PANEL_X, PANEL_W, PANEL_H, color);

    for(uint8_t slot = 0; slot < VISIBLE_ENTRIES && top + slot < n; slot++){
        const game_select_entry_t *entry = &entries[top + slot];
        uint8_t *dst = fb + (ICON_Y + slot * ICON_PITCH) * FB_WIDTH + ICON_X;
        if(entry->icon){
            game_select_blit_icon(dst, *entry->icon, ICON_W, ICON_H, UI_PALETTE);
        }
        else{
            stock_mario_ui_blit_4bpp(dst, stock_mario_ui_tile_ptr(entry->tile), ICON_W, ICON_H, UI_PALETTE >> 4);
        }
        if(top + slot == selected && !((*ui_blink_counter >> 5) & 1)){
            // Stock cursor position: left of the icon, vertically centered.
            stock_mario_ui_draw_tile16(dst + 16 * FB_WIDTH - 16, stock_mario_ui_tile_ptr(CURSOR_TILE));
        }
    }

    // Scroll hints in the gaps above/below the icons, in the panel border color.
    game_select_scroll_hints(fb, PANEL_X + PANEL_W / 2, ICON_Y, ICON_PITCH, ICON_H, top, n, VISIBLE_ENTRIES, color | 1);
}

#endif
