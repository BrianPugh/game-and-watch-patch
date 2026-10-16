#pragma once

/**
 * Stock functions are absolute symbols: the build turns every function in
 * tools/re/<device>.syms into stock_<name> (build/stock_symbols.s), so every
 * use is an ELF relocation the patcher can follow when it relocates stock code.
 * Declare a prototype here to call one.
 */
void stock_Reset_Handler(void);

gamepad_t stock_read_buttons(void);
