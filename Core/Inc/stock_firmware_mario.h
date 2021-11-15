#pragma once

/**
 *
 */
void (* const stock_Reset_Handler)(void) = 0x08017a45;


gamepad_t (* const stock_read_buttons)(void) = 0x08010d48 | THUMB;


/**
 * Returns `true` if USB power is connected, `false` otherwise.
 */
bool (* const is_usb_connected)(void) = 0x08010dc2 | THUMB;

/**
 * Put system to sleep.
 */
void (* const sleep)(void) = 0x080063a0 | THUMB;

/**
 * Address for checking to see if we are in {Clock, BALL, SMB1, SMB2}.
 * See `get_gnw_mode()`
 */
volatile uint8_t * const gnw_mode_addr = 0x20001044;

/**
 * This will most likely be overriden by the patcher.
 */
const uint8_t * const SMB1_ROM = 0x90001e60;

#define SMB1_CLOCK_WORKING 0x24000000
uint8_t * const smb1_clock_working = SMB1_CLOCK_WORKING;
uint8_t * const smb1_clock_graphics_working = SMB1_CLOCK_WORKING + 0x8000;


volatile uint8_t * const ui_draw_status_addr = 0x20010694;

/**
 * Function that loads the SMB1 rom into memory and prepares all the sprite
 * data.
 */
uint8_t * (* const stock_prepare_clock_rom)(uint8_t *src, size_t len) = 0x08010e10 | THUMB;
