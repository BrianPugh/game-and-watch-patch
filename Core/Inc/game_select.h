#pragma once

#include <stdbool.h>
#include <stdint.h>

/**
 * Helpers shared by both models' game-select DUAL BOOT entry
 * (game_select_mario.c, game_select_zelda.c).
 */

#define GAME_SELECT_FB_WIDTH 320

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
