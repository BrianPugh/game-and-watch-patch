#pragma once

#include <stddef.h>
#include <stdint.h>
#include <stdbool.h>

#define THUMB 0x00000001

typedef uint16_t gamepad_t;

/**
 *
 * Mapping
 * Note:
 *   * if UP is pressed, it explicitly disables DOWN (UP gets priority)
 *   * if RIGHT is pressed, it explicitly disables LEFT (RIGHT gets priority)
 *
 * Bit     Pin   Description
 * -------------------------
 *   0     PD15  RIGHT
 *   1     PD11  LEFT
 *   2     PD14  DOWN
 *   3     PD0   UP
 *   4     PC11  START  (if supported)
 *   5     PC12  SELECT (if supported)
 *   6     PD5   B
 *   7     PD9   A
 *   8     PC5   TIME
 *   9     PC13  PAUSE/SET
 *   10    PC1   GAME
 *   11
 *   12
 *   13
 *   14
 *   15
 */
#define GAMEPAD_RIGHT    ((gamepad_t) ( 1 <<  0 ))
#define GAMEPAD_LEFT     ((gamepad_t) ( 1 <<  1 ))
#define GAMEPAD_DOWN     ((gamepad_t) ( 1 <<  2 ))
#define GAMEPAD_UP       ((gamepad_t) ( 1 <<  3 ))
#define GAMEPAD_START    ((gamepad_t) ( 1 <<  4 ))
#define GAMEPAD_SELECT   ((gamepad_t) ( 1 <<  5 ))
#define GAMEPAD_B        ((gamepad_t) ( 1 <<  6 ))
#define GAMEPAD_A        ((gamepad_t) ( 1 <<  7 ))
#define GAMEPAD_TIME     ((gamepad_t) ( 1 <<  8 ))
#define GAMEPAD_PAUSE    ((gamepad_t) ( 1 <<  9 ))
#define GAMEPAD_GAME     ((gamepad_t) ( 1 << 10 ))
