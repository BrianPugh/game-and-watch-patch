#pragma once

#pragma GCC diagnostic push
#pragma GCC diagnostic ignored "-Wint-conversion"
#pragma GCC diagnostic ignored "-Wbuiltin-declaration-mismatch"

#include "stock_firmware_common.h"

#if GNW_DEVICE_MARIO
#include "stock_firmware_mario.h"
#elif GNW_DEVICE_ZELDA
#include "stock_firmware_zelda.h"
#else
#error "Invalid GNW Device specified."
#endif

#pragma GCC diagnostic pop
