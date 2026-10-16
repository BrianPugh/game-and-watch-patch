/**
 * Custom entries for the Mario game-select menu (press GAME).
 *
 * The stock menu shows 3 hard-coded icons (SMB1, SMB2, BALL). These hooks
 * replace its next/prev, input, and draw functions with a list of entries
 * shown through a scrolling window of 3 slots at the stock positions.
 *
 * Custom entries use ids that are not gnw_mode values. Stock code copies the
 * selected id into gnw_mode only after an A-press on an entry that differs
 * from the current mode, so custom ids are handled here before the stock
 * input handler ever sees them.
 *
 * Hook sites are in patches/mario.py; the reverse-engineered stock UI is
 * mapped in tools/re/mario.syms.
 */
#include "main.h"
#include "stock_firmware.h"

#if GNW_DEVICE_MARIO && GAME_SELECT_DUAL_BOOT

#define FB_WIDTH 320

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

#define UI_PALETTE 0xF
#define CURSOR_TILE 0x3B
#define DIRTY_RECT_GAME_SELECT 3

#define GAME_SELECT_ID_SMB1 0x10
#define GAME_SELECT_ID_SMB2 0x20
#define GAME_SELECT_ID_BALL 0x08
// Not a gnw_mode bit; must never reach gnw_mode.
#define GAME_SELECT_ID_DUAL_BOOT 0x0100

/**
 * Linear 4bpp (high nibble first) icon; filled in by the patcher.
 *
 * Must not be zero-initialized: the patcher locates free internal flash by
 * searching for a run of zeros, and would place data on top of this array.
 * 0x66 is the stock icon background color.
 */
const uint8_t GAME_SELECT_DUAL_BOOT_ICON[ICON_W * ICON_H / 2] = { [0 ... ICON_W * ICON_H / 2 - 1] = 0x66 };

typedef struct {
    uint16_t id;
    uint8_t tile;          // Stock iconset tile index, used if `icon` is NULL.
    const uint8_t *icon;   // Linear 4bpp ICON_W x ICON_H image.
} game_select_entry_t;

static const game_select_entry_t entries[] = {
    { GAME_SELECT_ID_SMB1, 0x41, NULL },
    { GAME_SELECT_ID_SMB2, 0x47, NULL },
    { GAME_SELECT_ID_BALL, 0x71, NULL },
    { GAME_SELECT_ID_DUAL_BOOT, 0, GAME_SELECT_DUAL_BOOT_ICON },
};

#define N_STOCK_ENTRIES 3
#define N_ENTRIES (sizeof(entries) / sizeof(entries[0]))

/**
 * Hide the patcher-populated array's contents from the optimizer; otherwise
 * reads from the all-zero initializer may be folded to constants.
 */
static inline const uint8_t *launder(const uint8_t *p){
    __asm__("" : "+r"(p));
    return p;
}

static uint8_t n_visible_entries(){
    // Only offer the dual-boot entry if there's something to boot.
    return dual_boot_target_valid() ? N_ENTRIES : N_STOCK_ENTRIES;
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

uint16_t game_select_next(uint16_t choice){
    uint8_t n = n_visible_entries();
    return entries[(entry_index(choice) + 1) % n].id;
}

uint16_t game_select_prev(uint16_t choice){
    uint8_t n = n_visible_entries();
    return entries[(entry_index(choice) + n - 1) % n].id;
}

void game_select_handle_input(gamepad_t held, gamepad_t pressed){
    if(pressed == GAMEPAD_A && held == pressed && *ui_game_select_choice == GAME_SELECT_ID_DUAL_BOOT){
        launch_dual_boot();
        // Target invalid; fall back to the current mode so the stock A
        // handler just closes the menu instead of switching to a bogus mode.
        *ui_game_select_choice = *ui_mode_on_open;
    }
    stock_game_select_handle_input(held, pressed);
}

static void blit_icon(uint8_t *dst, const uint8_t *src){
    for(uint32_t y = 0; y < ICON_H; y++){
        for(uint32_t x = 0; x < ICON_W; x += 2){
            uint8_t b = *src++;
            dst[x] = (b >> 4) | (UI_PALETTE << 4);
            dst[x + 1] = (b & 0xF) | (UI_PALETTE << 4);
        }
        dst += FB_WIDTH;
    }
}

/**
 * Draws a 7px-wide, 4px-tall triangle centered at `cx`; `y` is its flat edge.
 */
static void draw_arrow(uint8_t *fb, uint32_t cx, uint32_t y, bool up, uint8_t color){
    for(uint32_t row = 0; row < 4; row++){
        uint32_t half = up ? row : 3 - row;
        uint8_t *line = fb + (y + row) * FB_WIDTH + cx - half;
        for(uint32_t i = 0; i < 2 * half + 1; i++) line[i] = color;
    }
}

void game_select_draw(uint8_t *fb, ui_rect_t *rects, uint8_t color){
    static uint8_t top = 0;

    uint8_t n = n_visible_entries();
    uint8_t selected = entry_index(*ui_game_select_choice);

    // Keep the cursor inside the window.
    if(selected < top) top = selected;
    else if(selected >= top + VISIBLE_ENTRIES) top = selected - VISIBLE_ENTRIES + 1;
    if(top + VISIBLE_ENTRIES > n) top = n > VISIBLE_ENTRIES ? n - VISIBLE_ENTRIES : 0;

    ui_set_dirty_rect(&rects[DIRTY_RECT_GAME_SELECT], PANEL_X, PANEL_Y, PANEL_W, PANEL_H);
    ui_draw_panel(fb + PANEL_Y * FB_WIDTH + PANEL_X, PANEL_W, PANEL_H, color);

    for(uint8_t slot = 0; slot < VISIBLE_ENTRIES && top + slot < n; slot++){
        const game_select_entry_t *entry = &entries[top + slot];
        uint8_t *dst = fb + (ICON_Y + slot * ICON_PITCH) * FB_WIDTH + ICON_X;
        if(entry->icon){
            blit_icon(dst, launder(entry->icon));
        }
        else{
            ui_blit_4bpp(dst, ui_tile_ptr(entry->tile), ICON_W, ICON_H, UI_PALETTE);
        }
        if(top + slot == selected && !((*ui_blink_counter >> 5) & 1)){
            // Stock cursor position: left of the icon, vertically centered.
            ui_draw_tile16(dst + 16 * FB_WIDTH - 16, ui_tile_ptr(CURSOR_TILE));
        }
    }

    // Scroll hints in the gaps above/below the icons, in the panel border color.
    uint32_t cx = PANEL_X + PANEL_W / 2;
    if(top > 0){
        draw_arrow(fb, cx, ICON_Y - 7, true, color | 1);
    }
    if(top + VISIBLE_ENTRIES < n){
        draw_arrow(fb, cx, ICON_Y + (VISIBLE_ENTRIES - 1) * ICON_PITCH + ICON_H + 2, false, color | 1);
    }
}

#endif
