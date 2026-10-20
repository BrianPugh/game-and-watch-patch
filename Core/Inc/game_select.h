#pragma once

#include <stdbool.h>
#include <stdint.h>

#include "model.h"

/**
 * Helpers shared by both models' game-select custom entries
 * (game_select_mario.c, game_select_zelda.c).
 */

// The custom entries: the amalgam's switch to the other firmware (always),
// then the dual-boot target (--game-select-dual-boot).
#if GNW_AMALGAM || GAME_SELECT_DUAL_BOOT
#define GAME_SELECT_MENU 1
#endif

#define GAME_SELECT_FB_WIDTH 320

typedef struct {
    // Our pointer to the LZMA'd linear 4bpp (high nibble first) icon,
    // wherever the patcher put it: internal flash if it fits, else external.
    const uint8_t * const volatile *icon;
    // Where game_select_inflate_icons() inflates it.
    uint8_t *pixels;
    // Returns only if the target is invalid.
    void (*launch)(void);
} game_select_custom_t;

/**
 * How many of the `n` custom entries to show: the dual-boot target's (the
 * last) only if there's something to boot.
 */
uint8_t game_select_n_custom(uint8_t n);

/**
 * Inflates the `n` entries' icons (`size` bytes each) on the first call,
 * from the menu's first draw (it only draws while external flash is mapped).
 * The streams end with an end marker, so the input bound only has to be
 * large enough.
 */
void game_select_inflate_icons(const game_select_custom_t *custom, uint8_t n, uint32_t size, bool *ready);

/**
 * Moves the first visible entry `*top` of a `visible`-entry window over `n`
 * entries so it shows `selected`.
 */
void game_select_scroll(uint8_t *top, uint8_t selected, uint8_t n, uint8_t visible);

/**
 * Draws a linear 4bpp (high nibble first) w x h icon; each pixel is written
 * as `pal | nibble`.
 */
void game_select_blit_icon(uint8_t *dst, const uint8_t *src, uint32_t w, uint32_t h, uint8_t pal);

/**
 * Scroll hints: an up arrow above the first visible icon if entries are
 * hidden above, a down arrow below the last if entries are hidden below.
 * Icons are `icon_h` tall, `pitch` apart, the first at `y0`; arrows are
 * centered at `cx`.
 */
void game_select_scroll_hints(uint8_t *fb, uint32_t cx, uint32_t y0, uint32_t pitch, uint32_t icon_h,
                              uint8_t top, uint8_t n, uint8_t visible, uint8_t color);
