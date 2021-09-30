/**
 * Helpful source:
 *     https://zerosoft.zophar.net/ips.php
 */

#include <string.h>
#include "ips.h"

static char IPS_HEADER[] = {'P', 'A', 'T', 'C', 'H'};
static char IPS_TRAILER[] = {'E', 'O', 'F'};

#define COUNT_OF(x) ((sizeof(x)/sizeof(0[x])) / ((size_t)(!(sizeof(x) % sizeof(0[x])))))

#define BYTE3_TO_UINT(bp) \
    (((unsigned int)(bp)[0] << 16) & 0x00FF0000) | \
    (((unsigned int)(bp)[1] << 8) & 0x0000FF00) | \
    ((unsigned int)(bp)[2] & 0x000000FF)

#define BYTE2_TO_UINT(bp) \
    (((unsigned int)(bp)[0] << 8) & 0xFF00) | \
    ((unsigned int) (bp)[1] & 0x00FF)


/**
 * Assumes the ROM is already copied to dst and the patch will be applied inplace
 */
ips_patch_res_t ips_patch(uint8_t *dst, const uint8_t *patch){
    // Check Header Magic
    if(memcmp(patch, IPS_HEADER, COUNT_OF(IPS_HEADER))) return IPS_PATCH_WRONG_HEADER;
    patch += 5;

    // Iterate over Records until EOF trailer is hit.
    while(memcmp(patch, IPS_TRAILER, COUNT_OF(IPS_TRAILER))){
        uint32_t offset = BYTE3_TO_UINT(patch);  // We operate headerless
        patch += 3;

        uint16_t size = BYTE2_TO_UINT(patch);
        patch += 2;

        if(size){
            // Directly copy over
            memcpy(&dst[offset], patch, size);
            patch += size;
        }
        else{
            // RLE data
            size = BYTE2_TO_UINT(patch);
            patch += 2;

            uint8_t val = *patch++;

            memset(&dst[offset], val, size);
        }
    }

    return IPS_PATCH_OK;
}
