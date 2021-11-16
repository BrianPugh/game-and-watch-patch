from pathlib import Path

from .exception import InvalidStockRomError
from .firmware import Device, ExtFirmware, Firmware, IntFirmware
from .tileset import decode_backdrop
from .utils import fds_remove_crc_gaps, printi

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

    def _dump_roms(self):
        # English Zelda 1
        rom_addr = 0x3_0000
        rom_size = 0x2_0000
        (build_dir / "Legend of Zelda, The (USA).nes").write_bytes(
            b"NES\x1a\x08\x00\x12\x00\x00\x00\x00\x00\x00\x00\x00\x00"
            + self.external[rom_addr : rom_addr + rom_size]
        )

        # Japanse Zelda 1
        # This rom doesn't work :(
        rom_addr = 0x5_0000
        rom_size = 0x1_0000
        rom1 = bytearray(self.external[rom_addr : rom_addr + rom_size])
        # bios = self.external[0x5_E000:0x6_0000]
        rom1 = fds_remove_crc_gaps(rom1)
        rom_addr = 0x6_0000
        rom_size = 0x1_0000
        rom2 = fds_remove_crc_gaps(self.external[rom_addr : rom_addr + rom_size])
        (build_dir / "Zelda no Densetsu: The Hyrule Fantasy (J).fds").write_bytes(
            rom1 + rom2
        )

        # English Zelda 2
        rom_addr = 0x7_0000
        rom_size = 0x4_0000
        (build_dir / "Zelda II - Adventure of Link (USA).nes").write_bytes(
            b"NES\x1a\x08\x10\x12\x00\x00\x00\x00\x00\x00\x00\x00\x00"
            + self.external[rom_addr : rom_addr + rom_size]
        )

        # Japanse Zelda 2
        # This rom doesn't work :(
        rom_addr = 0xB_0000
        rom_size = 0x1_0000
        rom1 = bytearray(self.external[rom_addr : rom_addr + rom_size])
        # bios = self.external[0xB_E000:0xC_0000]
        rom1 = fds_remove_crc_gaps(rom1)
        rom_addr = 0xC_0000
        rom_size = 0x1_0000
        rom2 = fds_remove_crc_gaps(self.external[rom_addr : rom_addr + rom_size])
        (build_dir / "Link no Bouken - The Legend of Zelda 2 (J).fds").write_bytes(
            rom1 + rom2
        )

        # I Believe 0xD_0000 ~ 0xD_2000 are LoZ2-JP tweaks... or maybe just the timer?

        # English Link's Awakening
        # This rom doesn't work :(
        rom_addr = 0xD_2000
        rom_size = 0x8_0000
        (build_dir / "Legend of Zelda, The - Link's Awakening (en).gb").write_bytes(
            self.external[rom_addr : rom_addr + rom_size]
        )

    def _erase_roms(self):
        """Temporary for debugging, just seeing which roms impact the clock."""
        if False:
            # loz1-en is critical to clock
            rom_addr = 0x3_0000
            rom_size = 0x2_0000
            self.external.clear_range(rom_addr, rom_addr + rom_size)

        if True:
            # loz1-jp is not critical
            rom_addr = 0x5_0000
            rom_size = 0x2_0000
            self.external.clear_range(rom_addr, rom_addr + rom_size)

        if True:
            # loz2-en is not critical
            rom_addr = 0x7_0000
            rom_size = 0x4_0000
            self.external.clear_range(rom_addr, rom_addr + rom_size)

        if True:
            # loz2-jp is critical to timer; only crashes if timer is started.
            rom_addr = 0xB_0000
            rom_size = 0x2_0000
            self.external.clear_range(rom_addr, rom_addr + rom_size)

        if True:
            # Links Awakening (various)
            # 1,190,912 bytes
            rom_start = 0xD_2000
            rom_end = 0x1F_4C00
            self.external.clear_range(rom_start, rom_end)

    def _dump_backdrops(self):
        """Dump the 11 backdrop images.

        Overall length: 603,424 bytes

        Start       End
        --------    --------
        0x1F4C00    0x205a7d
        0x205A80    0x211913
        0x211920    0x213840
        0x213840    0x222500
        0x222500    0x234128
        0x234140    0x24247e
        0x242480    0x253949
        0x253960    0x25cf1f
        0x25CF20    0x26aaf8
        0x26AB00    0x279f98
        0x279FA0    0x28811d
        """
        bytes_starts = [
            ("0", 0x1F4C00),
            ("1", 0x205A80),
            ("2", 0x211920),
            ("3", 0x213840),
            ("4", 0x222500),
            ("5", 0x234140),
            ("6", 0x242480),
            ("7", 0x253960),
            ("8", 0x25CF20),
            ("9", 0x26AB00),
            ("10", 0x279FA0),
        ]
        for name, start in bytes_starts:
            img, consumed = decode_backdrop(self.external[start:])
            img.save(build_dir / f"backdrop_{name}.png")
            print(hex(start + consumed))

    def patch(self):
        self._dump_roms()
        self._dump_backdrops()

        if False:
            self._erase_roms()

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
