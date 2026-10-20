/**
 * Custom entries in the game-select menu (press GAME): in the amalgam, a
 * switch to the other firmware, and a DUAL BOOT entry
 * (--game-select-dual-boot).
 *
 * Mario (game_select_mario.c): the stock menu shows 3 hard-coded icons (SMB1,
 * SMB2, BALL). Hooks replace its next/prev, input, and draw functions with a
 * list of entries shown through a scrolling window of 3 slots at the stock
 * positions.
 *
 * Zelda (game_select_zelda.c): the stock menu fills the screen with 5 rows.
 * The entries follow the stock rows (from the 6th row, or the 5th with
 * --no-la), and the stock draw function shows a window of 5 rows.
 *
 * This file holds the drawing and scrolling both share. Hook sites are in
 * patches/<device>.py; the reverse-engineered stock UI is mapped in
 * tools/re/<device>.syms.
 */
#include "main.h"
#include "game_select.h"

uint8_t game_select_n_custom(uint8_t n){
#if GAME_SELECT_DUAL_BOOT
    if(!dual_boot_target_valid()) return n - 1;
#endif
    return n;
}

void game_select_inflate_icons(const game_select_custom_t *custom, uint8_t n, uint32_t size, bool *ready){
    if(*ready) return;
    for(uint8_t i = 0; i < n; i++){
        memcpy_inflate(custom[i].pixels, *custom[i].icon, 2 * size);
    }
    *ready = true;
}

void game_select_scroll(uint8_t *top, uint8_t selected, uint8_t n, uint8_t visible){
    if(selected < *top) *top = selected;
    else if(selected >= *top + visible) *top = selected - visible + 1;
    if(*top + visible > n) *top = n > visible ? n - visible : 0;
}

void game_select_blit_icon(uint8_t *dst, const uint8_t *src, uint32_t w, uint32_t h, uint8_t pal){
    for(uint32_t y = 0; y < h; y++){
        for(uint32_t x = 0; x < w; x += 2){
            uint8_t b = *src++;
            dst[x] = pal | (b >> 4);
            dst[x + 1] = pal | (b & 0xF);
        }
        dst += GAME_SELECT_FB_WIDTH;
    }
}

/**
 * Draws a 7px-wide, 4px-tall triangle centered at `cx`; `y` is its top row.
 */
static void draw_arrow(uint8_t *fb, uint32_t cx, uint32_t y, bool up, uint8_t color){
    for(uint32_t row = 0; row < 4; row++){
        uint32_t half = up ? row : 3 - row;
        uint8_t *line = fb + (y + row) * GAME_SELECT_FB_WIDTH + cx - half;
        for(uint32_t i = 0; i < 2 * half + 1; i++) line[i] = color;
    }
}

void game_select_scroll_hints(uint8_t *fb, uint32_t cx, uint32_t y0, uint32_t pitch, uint32_t icon_h,
                              uint8_t top, uint8_t n, uint8_t visible, uint8_t color){
    if(top > 0){
        draw_arrow(fb, cx, y0 - 7, true, color);
    }
    if(top + visible < n){
        draw_arrow(fb, cx, y0 + (visible - 1) * pitch + icon_h + 2, false, color);
    }
}
