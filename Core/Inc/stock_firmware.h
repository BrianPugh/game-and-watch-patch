#pragma once

#pragma GCC diagnostic push
#pragma GCC diagnostic ignored "-Wint-conversion"
#pragma GCC diagnostic ignored "-Wbuiltin-declaration-mismatch"

#include "stock_firmware_common.h"

#if !GNW_DEVICE_MARIO && !GNW_DEVICE_ZELDA
#error "Invalid GNW Device specified."
#endif

// The amalgam build (--device=amalgam) defines both.
#if GNW_DEVICE_MARIO
#include "stock_firmware_mario.h"
#endif
#if GNW_DEVICE_ZELDA
#include "stock_firmware_zelda.h"
#endif

#pragma GCC diagnostic pop
