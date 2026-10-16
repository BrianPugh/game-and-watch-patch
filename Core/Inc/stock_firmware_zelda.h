#pragma once

/**
 * Stock functions are absolute symbols: the build turns every function in
 * tools/re/<device>.syms into stock_<name> (build/stock_symbols.s), so every
 * use is an ELF relocation the patcher can follow when it relocates stock code.
 * Declare a prototype here to call one.
 */
void stock_Reset_Handler(void);

gamepad_t stock_read_buttons(void);

#define ZELDA_BACKDROP_COUNT 11
// DTCM .data: pointers to the backdrop images in external flash.
static const uint8_t * const * const stock_backdrop_images = (const uint8_t * const *)0x2000a944;
