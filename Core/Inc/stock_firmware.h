#pragma once

#pragma GCC diagnostic push
#pragma GCC diagnostic ignored "-Wint-conversion"
#pragma GCC diagnostic ignored "-Wbuiltin-declaration-mismatch"

#include <stddef.h>
#include <stdint.h>
#include <stdbool.h>

#define THUMB 0x00000001

/**
 *
 */
void (* const stock_Reset_Handler)(void) = 0x08017a45;


/**
 *
 * Mapping
 * Note:
 *   * if UP is pressed, it explicitly disables DOWN (UP gets priority)
 *   * if RIGHT is pressed, it explicitly disables LEFT (RIGHT gets priority)
 *
 * Bit     Description
 * -------------------
 *   0     RIGHT
 *   1     LEFT
 *   2     DOWN
 *   3     UP
 *   4
 *   5
 *   6     B
 *   7     A
 *   8     TIME
 *   9     PAUSE/SET
 *   10    GAME
 *   11
 *   12
 *   13
 *   14
 *   15
 */
typedef uint16_t gamepad_t;
gamepad_t (* const stock_read_buttons)(void) = 0x08010d48 | THUMB;

#define GAMEPAD_RIGHT ((gamepad_t) ( 1 <<  0 ))
#define GAMEPAD_LEFT  ((gamepad_t) ( 1 <<  1 ))
#define GAMEPAD_DOWN  ((gamepad_t) ( 1 <<  2 ))
#define GAMEPAD_UP    ((gamepad_t) ( 1 <<  3 ))
#define GAMEPAD_B     ((gamepad_t) ( 1 <<  6 ))
#define GAMEPAD_A     ((gamepad_t) ( 1 <<  7 ))
#define GAMEPAD_TIME  ((gamepad_t) ( 1 <<  8 ))
#define GAMEPAD_PAUSE ((gamepad_t) ( 1 <<  9 ))
#define GAMEPAD_GAME  ((gamepad_t) ( 1 << 10 ))

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
 * Returns `true` if the menu is open (pause/set button menu)
 */
bool is_menu_open();

/**
 * Function that loads the SMB1 rom into memory and prepares all the sprite
 * data.
 */
uint8_t * (* const stock_prepare_clock_rom)(uint8_t *src, size_t len) = 0x08010e10 | THUMB;

#pragma GCC diagnostic pop
