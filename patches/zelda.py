from pathlib import Path

from PIL import Image

import patches

from .compression import lzma_compress
from .exception import BadImageError, InvalidStockRomError
from .firmware import Device, ExtFirmware, Firmware, IntFirmware
from .tileset import bytes_to_tilemap, tilemap_to_bytes
from .utils import (
    printd,
    printe,
    printi,
    round_down_word,
    round_up_page,
    seconds_to_frames,
)

build_dir = Path("build")  # TODO: expose this properly or put in better location


class ZeldaGnW(Device, name="zelda"):
    class Int(IntFirmware):
        STOCK_ROM_SHA1_HASH = "ac14bcea6e4ff68c88fd2302c021025a2fb47940"
        STOCK_ROM_END = 0x1B6E0  # Used for generating linker script.
        KEY_OFFSET = 0x165A4
        NONCE_OFFSET = 0x16590
        # RWDATA_OFFSET = 0x1B390
        RWDATA_LEN = 20
        RWDATA_DTCM_IDX = 0  # decompresses to 0x2000_A800

    class Ext(ExtFirmware):
        STOCK_ROM_SHA1_HASH = "1c1c0ed66d07324e560dcd9e86a322ec5e4c1e96"
        ENC_START = 0x20000
        ENC_END = 0x3254A0

        def _verify(self):
            h = self.hash(self[self.ENC_START : self.ENC_END])
            if h != self.STOCK_ROM_SHA1_HASH:
                raise InvalidStockRomError

    class FreeMemory(Firmware):
        FLASH_BASE = 0x240F2124
        FLASH_LEN = 0  # 0x24100000 - FLASH_BASE

    def argparse(self, parser):
        self.args = parser.parse_args()
        return self.args

    def patch(self):
        printi("Invoke custom bootloader prior to calling stock Reset_Handler.")
        self.internal.replace(0x4, "bootloader")

        printi("Intercept button presses for macros.")
        self.internal.bl(0xFE54, "read_buttons")

        if not self.args.encrypt:
            # Disable OTFDEC
            self.internal.nop(0x16536, 2)
            self.internal.nop(0x1653A, 1)
            self.internal.nop(0x1653C, 1)

        internal_remaining_free = len(self.internal) - self.int_pos
        compressed_memory_free = (
            len(self.compressed_memory) - self.compressed_memory_pos
        )

        return internal_remaining_free, compressed_memory_free
