#pragma once

/**
 * Which stock firmware is running.
 *
 * Single-model builds know at compile time. The amalgam build (both
 * GNW_DEVICE_MARIO and GNW_DEVICE_ZELDA) holds both firmwares in bank 1:
 * Mario's stock code stays at the start of the bank and Zelda's runs from its
 * own vector table elsewhere, so shared code tells them apart by SCB->VTOR.
 */

#include "stm32h7xx.h"

#if GNW_DEVICE_MARIO && GNW_DEVICE_ZELDA
#define GNW_AMALGAM 1
#define RUNNING_MARIO() (SCB->VTOR == FLASH_BANK1_BASE)
#elif GNW_DEVICE_MARIO
#define RUNNING_MARIO() 1
#else
#define RUNNING_MARIO() 0
#endif

/**
 * Mario's or Zelda's expression for the running firmware. A single-model
 * build drops the other one before compiling, so it may name symbols that
 * build doesn't define.
 */
#if GNW_AMALGAM
#define MODEL_SELECT(mario, zelda) (RUNNING_MARIO() ? (mario) : (zelda))
#elif GNW_DEVICE_MARIO
#define MODEL_SELECT(mario, zelda) (mario)
#else
#define MODEL_SELECT(mario, zelda) (zelda)
#endif
