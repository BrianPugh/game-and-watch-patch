#pragma once

#include <stddef.h>
#include <stdint.h>
#include <stdbool.h>

enum {
    IPS_PATCH_OK = 0,
    IPS_PATCH_WRONG_HEADER,
};
typedef uint8_t ips_patch_res_t;

ips_patch_res_t ips_patch(uint8_t *dst, const uint8_t *patch);
