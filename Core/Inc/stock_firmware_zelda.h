#pragma once

/**
 * Stock functions are absolute symbols (generated into build/stock_symbols.s from
 * STOCK_SYMBOLS in patches/<device>.py), so every use is an ELF relocation
 * the patcher can follow when it relocates stock code.
 */
void stock_Reset_Handler(void);

gamepad_t stock_read_buttons(void);
