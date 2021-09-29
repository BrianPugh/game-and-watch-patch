#include <string.h>
#include "ips.h"

static char IPS_HEADER[] = {'P', 'A', 'T', 'C', 'H'};

ips_patch_res_t ips_patch(uint8_t *dst, const uint8_t *src, const uint8_t *patch){
    // Check Header Magic
    if(memcmp(src, IPS_HEADER, 5)) return IPS_PATCH_WRONG_HEADER;

    //TODO

    return IPS_PATCH_OK;
}
