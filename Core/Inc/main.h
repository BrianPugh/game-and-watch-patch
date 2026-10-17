/* USER CODE BEGIN Header */
/**
  ******************************************************************************
  * @file           : main.h
  * @brief          : Header for our C code (main.c is shared by both models).
  *                   This file contains the common defines of the application.
  ******************************************************************************
  * @attention
  *
  * <h2><center>&copy; Copyright (c) 2020 STMicroelectronics.
  * All rights reserved.</center></h2>
  *
  * This software component is licensed by ST under BSD 3-Clause license,
  * the "License"; You may not use this file except in compliance with the
  * License. You may obtain a copy of the License at:
  *                        opensource.org/licenses/BSD-3-Clause
  *
  ******************************************************************************
  */
/* USER CODE END Header */

/* Define to prevent recursive inclusion -------------------------------------*/
#ifndef __MAIN_H
#define __MAIN_H

#ifdef __cplusplus
extern "C" {
#endif

/* Includes ------------------------------------------------------------------*/
#include "stock_firmware.h"
#include "model.h"

/* Private includes ----------------------------------------------------------*/
/* USER CODE BEGIN Includes */

/* USER CODE END Includes */

/* Exported types ------------------------------------------------------------*/
/* USER CODE BEGIN ET */

/* USER CODE END ET */

/* Exported constants --------------------------------------------------------*/
/* USER CODE BEGIN EC */

/* USER CODE END EC */

/* Exported macro ------------------------------------------------------------*/
/* USER CODE BEGIN EM */

/* USER CODE END EM */

/* Exported functions prototypes ---------------------------------------------*/
void Error_Handler(void);

/**
 * Returns `true` if the menu is open (pause/set button menu)
 */
bool is_menu_open();


/* USER CODE BEGIN EFP */

/* USER CODE END EFP */

/* Private defines -----------------------------------------------------------*/
#define GPIO_Speaker_enable_Pin GPIO_PIN_3
#define GPIO_Speaker_enable_GPIO_Port GPIOE
#define BTN_PAUSE_Pin GPIO_PIN_13
#define BTN_PAUSE_GPIO_Port GPIOC
#define BTN_GAME_Pin GPIO_PIN_1
#define BTN_GAME_GPIO_Port GPIOC
#define BTN_TIME_Pin GPIO_PIN_5
#define BTN_TIME_GPIO_Port GPIOC
#define BTN_A_Pin GPIO_PIN_9
#define BTN_A_GPIO_Port GPIOD
#define BTN_Left_Pin GPIO_PIN_11
#define BTN_Left_GPIO_Port GPIOD
#define BTN_Down_Pin GPIO_PIN_14
#define BTN_Down_GPIO_Port GPIOD
#define BTN_Right_Pin GPIO_PIN_15
#define BTN_Right_GPIO_Port GPIOD
#define BTN_Up_Pin GPIO_PIN_0
#define BTN_Up_GPIO_Port GPIOD
#define BTN_B_Pin GPIO_PIN_5
#define BTN_B_GPIO_Port GPIOD
/* USER CODE BEGIN Private defines */

/* USER CODE END Private defines */

enum {
    GNW_MODE_CLOCK,
    GNW_MODE_BALL,
    GNW_MODE_SMB1,
    GNW_MODE_SMB2,
};
typedef uint8_t gnw_mode_t;

gnw_mode_t get_gnw_mode();

void *memcpy_inflate(uint8_t *dst, const uint8_t *src, size_t n);

// LzmaDecode only allocates the probability model (the output buffer is the
// dictionary): (1984 + 0x300 << (lc + lp)) UInt16s. Each firmware has its own
// (mario.c, zelda.c), in its RAM.
#define LZMA_BUF_SIZE            ((1984 + (0x300 << 0)) * 2)
extern uint8_t mario_lzma_heap[LZMA_BUF_SIZE];
extern uint8_t zelda_lzma_heap[LZMA_BUF_SIZE];

uint8_t sleep_image_inflate(const uint8_t *src, uint16_t *dst);

/**
 * --smb1-graphics: DOWN on the clock screen cycles the SMB1 graphics (mario.c).
 */
void smb1_graphics_buttons(gamepad_t gamepad);

/**
 * Returns `true` if the dual-boot target (bank 2, or the SD bootloader) has a
 * plausible vector table.
 */
bool dual_boot_target_valid();

/**
 * Reset into the dual-boot target. Returns only if the target is invalid.
 */
void launch_dual_boot();

/**
 * The game-select menu's DUAL BOOT entry: the other firmware in the amalgam
 * (always there), else the dual-boot target. game_select_launch() returns
 * only if the target is invalid.
 */
#if GNW_AMALGAM
bool game_select_target_valid();
void game_select_launch();
#else
#define game_select_target_valid dual_boot_target_valid
#define game_select_launch launch_dual_boot
#endif

#ifdef __cplusplus
}
#endif

#endif /* __MAIN_H */

/************************ (C) COPYRIGHT STMicroelectronics *****END OF FILE****/
