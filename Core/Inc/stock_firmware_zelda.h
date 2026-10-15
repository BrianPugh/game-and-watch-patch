#pragma once

static void (* const stock_Reset_Handler)(void) = 0x0801ad49;

static gamepad_t (* const stock_read_buttons)(void) = 0x08016808 | THUMB;
