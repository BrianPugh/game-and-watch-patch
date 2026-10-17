#pragma once

#include "model.h"

extern uint32_t _etext;

#if GNW_AMALGAM
// Each firmware's RAM (STM32H7B0VBTx_FLASH_amalgam.ld): our Mario-only
// objects' .data/.bss in Mario's free RAM, the Zelda-only ones in Zelda's.
extern uint32_t _mario_sbss, _mario_ebss, _mario_sidata, _mario_sdata, _mario_edata;
extern uint32_t _zelda_sbss, _zelda_ebss, _zelda_sidata, _zelda_sdata, _zelda_edata;
#define MODEL_RAM(sym) MODEL_SELECT(&_mario##sym, &_zelda##sym)
#else
extern uint32_t _sbss;
extern uint32_t _ebss;
extern uint32_t _sidata;
extern uint32_t _sdata;
extern uint32_t _edata;
#define MODEL_RAM(sym) (&sym)
#endif
