#pragma once

#include <stdint.h>

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
 *   16
 *   17
 *   18
 *   19
 *   20
 *   21
 *   22
 *   23
 *   24
 *   25
 *   26
 *   27
 *   28
 *   29
 *   30
 *   31
 */
typedef uint32_t gamepad_t;
gamepad_t (* const stock_read_buttons)(void) = 0x08010d48 | THUMB;


#define GAMEPAD_RIGHT ( 1 << 0 )
#define GAMEPAD_LEFT  ( 1 << 1 )
#define GAMEPAD_DOWN  ( 1 << 2 )
#define GAMEPAD_UP    ( 1 << 3 )
#define GAMEPAD_B     ( 1 << 6 )
#define GAMEPAD_A     ( 1 << 7 )
#define GAMEPAD_TIME  ( 1 << 8 )
#define GAMEPAD_PAUSE ( 1 << 9 )
#define GAMEPAD_GAME  ( 1 << 10 )
