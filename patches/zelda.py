"""
Start      End        Description
---------- ---------- --------------------------------
0x00000    0x00090    If cleared, factory start. Stores Vermin score.
0x01000    0x01090    ^ second bank

0x02000    0x02B20    LA JP Save1
0x03000    0x03B20    LA JP Save2
0x04000    0x04B20    LA EN Save1
0x05000    0x05B20    LA EN Save2
0x06000    0x06B20    LA FR Save1
0x07000    0x07B20    LA FR Save2
0x08000    0x08B20    LA DE Save1
0x09000    0x09B20    LA DE Save2

0x0A000    0x0A560    LoZ1 EN Save1
0x0B000    0x0B560    LoZ1 EN Save2
0x0C000    0x0C540    LoZ1 JP Save1
0x0D000    0x0D540    LoZ1 JP Save2

0x0E000    0x0E360    LoZ2 EN Save1
0x0F000    0x0F360    LoZ2 EN Save2
0x10000    0x10360    LoZ2 JP Save1
0x11000    0x11360    LoZ2 JP Save2

0x12000    0x13000    Factory Test Scratch Pad

0x13000    0z20000    Empty

0x20000    0x30000    Sprites?

0x30000    0x50000    LoZ1 EN ROM
0x50000    0x70000    LoZ1 JP ROM

0x70000    0xB0000    LoZ2 EN ROM
0xB0000    0xD0000    LoZ2 JP ROM

0xD0000    0xD2000    LoZ2 Timer stuff?

0xD2000    0x1F4C00   LA ROMs (1,190,912 bytes)

0x1f4c00   0x288120   The 11 Backdrop Images (603,424 bytes)

0x288120   0x325490   External FW Data (643,952 bytes)

0x325490   0x3E0000   Empty  (764,784 bytes)

0x3E0000   0x400000   LA save states, 32 KB per LA language

The patch keeps 0x00000-0x20000 in place, closes the gaps after it, and puts
the LA save states right after the data (see ZeldaGnW._compact_ext and
_finish_ext).
"""


from pathlib import Path

import numpy as np

from . import writelog
from .compression import lzma_compress
from .exception import InvalidStockRomError
from .firmware import Device, ExtFirmware, Firmware, IntFirmware
from .thumb_asm import assemble as thumb_assemble
from .tileset import (
    ZELDA_GAME_SELECT_PALETTE,
    ZELDA_ICON_ROLES,
    decode_backdrop,
    png_to_icon_4bpp,
    render_label_icon,
)
from .utils import fds_remove_crc_gaps, printd, printi, round_up_page, round_up_word
from .xip import XipUnit

build_dir = Path("build")  # TODO: expose this properly or put in better location

# Stock external offsets of the 11 backdrop images (backdrop_images in DTCM),
# and the end of the last one's zero padding.
BACKDROP_STARTS = [
    0x1F_4C00,
    0x20_5A80,
    0x21_1920,
    0x21_3840,
    0x22_2500,
    0x23_4140,
    0x24_2480,
    0x25_3960,
    0x25_CF20,
    0x26_AB00,
    0x27_9FA0,
]
BACKDROP_END = 0x28_8120

# itcm_overlay_load's 5 copies out of external flash (4 into ITCM, 1 into
# DTCM), which fill 0x30B4A0-0x317230: (source literal, stock offset, size).
OVERLAY_COPIES = [
    (0xFD30, 0x30_B4A0, 0xEE8),
    (0xFD38, 0x30_C388, 0x1E70),
    (0xFD44, 0x30_E1F8, 0x5D0),
    (0xFD50, 0x30_E7C8, 0x6F24),
    (0xFD5C, 0x31_56EC, 0x1B44),
]
OVERLAY_START, OVERLAY_END = 0x30_B4A0, 0x31_7230

# Vermin's code: its API (called from main_loop_tick and app_load), game,
# sound and high-score save.
VERMIN_UNIT = (0x0801_4938, 0x0801_6268)
# The last copy is the menu UI, linked at ITCM 0x9C7C. ITCM past its end is
# never loaded, so veneers from ITCM to our code are appended to it.
ITCM_MENU, ITCM_MENU_SRC, ITCM_MENU_SIZE = 0x9C7C, 0x31_56EC, 0x1B44


class ZeldaGnW(Device, name="zelda"):
    # --relocate-stock moves everything below the init table, vector table
    # included; the amalgam moves it to the top of bank 1.
    STOCK_RELOCATE_END = 0x0801_B368
    relocates_in_amalgam = True
    xip_vermin = None

    class Int(IntFirmware):
        STOCK_ROM_SHA1_HASH = "ac14bcea6e4ff68c88fd2302c021025a2fb47940"
        STOCK_ROM_END = 0x1B3E0  # Used for generating linker script.
        KEY_OFFSET = 0x165A4
        NONCE_OFFSET = 0x16590
        RWDATA_OFFSET = 0x1B390
        RWDATA_LEN = 20
        RWDATA_DTCM_IDX = 0  # decompresses to 0x2000_A800
        # Stock functions our C code calls (Thumb addresses); see MarioGnW.Int.

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

    def add_arguments(self, parser):
        group = parser.add_argument_group("Low level flash savings flags")
        group.add_argument(
            "--no-la",
            action="store_true",
            help="Remove Link's Awakening rom (all languages) and its game-select row.",
        )
        group.add_argument(
            "--no-sleep-images",
            action="store_true",
            help="Remove the 11 backdrop images (sleep and first-boot screens).",
        )
        group.add_argument(
            "--loz1",
            type=Path,
            default=None,
            help="Override LoZ1 ROM with your own file.",
        )
        group.add_argument(
            "--loz1j",
            type=Path,
            default=None,
            help="Override Japanese LoZ1 (FDS) ROM with your own file.",
        )
        group.add_argument(
            "--loz2",
            type=Path,
            default=None,
            help="Override LoZ2 ROM with your own file.",
        )

        group.add_argument(
            "--no-hour-tune",
            action="store_true",
            help="Remove the hour tune in TIME/CLOCK.",
        )

        group.add_argument(
            "--no-factory-test",
            action="store_true",
            help="Remove the factory CHARGE AND KEY TEST screens (~9KB of internal code).",
        )

        group = parser.add_argument_group("Game-select menu")
        group.add_argument(
            "--game-select-dual-boot",
            action="store_true",
            help="Add a row to the GAME menu that launches the dual-boot target "
            "(same as LEFT+GAME). Only shown if a valid target is flashed.",
        )
        mgroup = group.add_mutually_exclusive_group()
        mgroup.add_argument(
            "--game-select-dual-boot-icon",
            type=Path,
            default=Path(__file__).parent / "assets" / "retro_go_icon_zelda.png",
            help="96x32 image for the dual-boot menu row; colors are mapped to the "
            "nearest menu palette color. Defaults to a Retro-Go logo.",
        )
        mgroup.add_argument(
            "--game-select-dual-boot-label",
            type=str,
            default=None,
            help="Instead of an image, draw this text on the dual-boot menu row's icon.",
        )

        group = parser.add_argument_group("Experimental")
        group.add_argument(
            "--relocate-stock",
            type=lambda s: int(s, 0),
            metavar="ADDR",
            help="Run the stock firmware relocated to this address: in bank 2 "
            "(e.g. 0x08120000, written to a separate bank-2 image) or past our "
            "code in bank 1 (e.g. 0x08020000 with --extended). "
            "Tests stock-code relocation; see tools/emu/README.md.",
        )
        group.add_argument(
            "--xip-minigame",
            action="store_true",
            help="Run Vermin's code from external flash (frees ~6KB of internal flash).",
        )

    def _itcm_menu_offset(self, itcm):
        """External flash offset of ITCM address ``itcm`` in the menu UI copy."""
        return self.overlay_pos + ITCM_MENU_SRC - OVERLAY_START + itcm - ITCM_MENU

    def _itcm_menu_patch(self, itcm, code, expect):
        """Assemble ``code`` at ITCM address ``itcm`` in the menu UI copy."""
        offset = self._itcm_menu_offset(itcm)
        self.external.replace(offset, bytes(thumb_assemble(code, itcm)), expect=expect)

    def _itcm_veneer(self, symbol):
        """ITCM address of a new ``ldr.w pc`` veneer to one of our functions."""
        itcm = ITCM_MENU + ITCM_MENU_SIZE + len(self.itcm_tail)
        target = self.internal.address(symbol) | 1
        self.patcher_funcs.append((itcm, f"{symbol}_veneer", 4))
        self.itcm_tail += bytes(thumb_assemble("ldr.w pc, [pc, #0]"))
        self.itcm_tail += target.to_bytes(4, "little")
        self.itcm_tail_write = writelog.new(
            "ITCM veneers to our code, after the menu UI copy"
        )
        return itcm

    def _second_beep_setting(self):
        """TICK ON/OFF: a 5th row in the clock's PAUSE/SET overlay.

        The setting is bit 2 of nvram word +0x28 (nvram_ram 0x240ff000). See
        second_beep_draw/second_beep_input in zelda.c.
        """
        printi("Add a TICK ON/OFF row to the clock settings overlay.")
        # clock_tick queues the hour tune (0x60) or the second beep (0x40) into
        # LoZ1's sound register (nes RAM 0x603) when the second changes. The
        # 4 hour-tune bytes are [r8, #24..27] (r1 = byte 27); r2/r3 are dead
        # after the block (the next call takes r0/r1).
        hour = "b 0x90320016" if self.args.no_hour_tune else "cbnz r2, 0x90320016"
        block = "; ".join(
            [
                "add.w r3, r9, #0x600",
                "ldr.w r2, [r8, #24]",
                hour,
                "movs r0, #0x60",
                "strb r0, [r3, #3]",
                "b 0x90320030",
                # 0x90320016: the second beep, unless the setting is off.
                "ldr.w r0, [pc, #0x10]",
                "ldr.w r0, [r0]",
                "lsls r0, r0, #29",
                "bmi 0x90320030",
                "movs r1, #0x40",
                "strb r1, [r3, #3]",
                "b 0x90320030",
            ]
        )
        code = bytes(thumb_assemble(block, 0x9032_0006))
        code += (0x240F_F028).to_bytes(4, "little") + bytes(thumb_assemble("nop; nop"))
        assert len(code) == 0x2A, len(code)
        self.external.replace(0x32_0006, code, expect="42:70a3d5df")

        # settings_clock_handle_input: 5 rows; row 4 goes to second_beep_input
        # (rows past 3 used to reset the cursor to 0).
        self._itcm_menu_patch(0xA620, "movs r1, #5", expect="2:69dcc7a5")
        veneer = self._itcm_veneer("settings_row_input")
        self._itcm_menu_patch(0xA63E, f"b.w #{veneer:#x}", expect="4:59165ff1")
        # settings_clock_draw: rows 24 lines apart instead of 32 (zelda.c's
        # SETTINGS_ROW_STEP), and second_beep_draw after the AUTO SLEEP text.
        self._itcm_menu_patch(0xAF3E, "mov.w r4, #0x1e00", expect="4:a97cfd34")
        veneer = self._itcm_veneer("settings_draw_tail")
        self._itcm_menu_patch(0xAFBA, f"b.w #{veneer:#x}", expect="4:7ab9482a")

    def _game_select_drop_la_row(self):
        """Remove LA's game-select row; VERMIN and TIMER move up into its place.

        The 5th row is left empty (a count of 0, which game_select_draw skips),
        and the UP/DOWN cursor wraps over 4 rows. With --game-select-dual-boot,
        DUAL BOOT takes the empty row (game_select_zelda.c counts the rows).
        """
        printi("Remove the LA row from the game-select menu.")
        # menu_rows: 5 rows x 11 bytes, [count, variant ids...]; LA's are 4-7.
        menu_rows, size = 0x2D_F1B0, 11
        rows = [
            bytes(self.external[menu_rows + i * size : menu_rows + (i + 1) * size])
            for i in range(5)
        ]
        kept = [row for row in rows if not set(row[1 : 1 + row[0]]) & {4, 5, 6, 7}]
        assert len(kept) == 4, rows
        data = b"".join(kept) + bytes(size)
        self.external.replace(menu_rows, data, expect="55:22f5ca23")
        # menu_app_id_to_row, which places the cursor when the menu opens.
        self._itcm_menu_patch(0xA156, "movs r0, #0", expect="2:488150b5")  # LA
        self._itcm_menu_patch(0xA15A, "movs r0, #2", expect="2:519a61f4")  # VERMIN
        self._itcm_menu_patch(0xA15E, "movs r0, #3", expect="2:1edbf733")  # TIMER
        # game_select_handle_input's UP/DOWN step (--game-select-dual-boot
        # replaces the call and counts the rows itself).
        self._itcm_menu_patch(0xA572, "movs r1, #4", expect="2:70c7f6e4")

    def _game_select_dual_boot(self):
        """DUAL BOOT after the stock game-select rows, in a scrolling 5-row window.

        See game_select_zelda.c. Runs after fix_ext_refs(), so
        the menu_rows literals hold their final address.
        """
        printi("Add dual-boot entry to the game-select menu.")
        # game_select_handle_input's UP/DOWN step over the 5 rows.
        veneer = self._itcm_veneer("zelda_game_select_row_step")
        self._itcm_menu_patch(0xA576, f"bl #{veneer:#x}", expect="4:9be285a5")
        # ui_handle_input's call to game_select_handle_input.
        veneer = self._itcm_veneer("zelda_game_select_handle_input")
        self._itcm_menu_patch(0xA886, f"bl #{veneer:#x}", expect="4:3066bdda")
        # ui_render_overlay's call to game_select_draw.
        veneer = self._itcm_veneer("zelda_game_select_draw")
        self._itcm_menu_patch(0xB532, f"bl #{veneer:#x}", expect="4:937c01f9")
        # game_select_draw's 2 loads of menu_rows (0x0000acfa, 0x0000ad76) use
        # the literal at 0x0000b788, which nothing else reads: point it at the
        # rows in the window. 0x0000ac60 holds menu_rows for everything else.
        literal = self._itcm_menu_offset(0xB788)
        menu_rows = self.external.int(self._itcm_menu_offset(0xAC60))
        if self.external.int(literal) != menu_rows:
            raise ValueError("game_select_draw's menu_rows literal moved")
        self.external.replace(
            literal, self.internal.address("zelda_game_select_view_rows"), size=4
        )

        if self.args.game_select_dual_boot_label:
            icon = render_label_icon(
                self.args.game_select_dual_boot_label, 96, 32, ZELDA_ICON_ROLES
            )
        else:
            icon = png_to_icon_4bpp(
                self.args.game_select_dual_boot_icon, 96, 32, ZELDA_GAME_SELECT_PALETTE
            )
        self.internal.replace(
            self.internal.address("ZELDA_GAME_SELECT_DUAL_BOOT_ICON", sub_base=True),
            icon,
        )

    def _flash_roms(self):
        # English Zelda 1
        if self.args.loz1:
            loz1_addr, loz1_size = 0x3_0000, 0x2_0000
            loz1 = self.args.loz1.read_bytes()
            # Remove the NES header
            if loz1[0] == 0x4E:
                loz1 = loz1[16:]
            self.external[loz1_addr : loz1_addr + loz1_size] = loz1

        # Japanese Zelda 1 (FDS)
        if self.args.loz1j:
            loz1j_addr, loz1j_size = 0x5_0000, 0x2_0000
            loz1j = self.args.loz1j.read_bytes()
            # Remove the NES header
            if loz1j[0] == 0x46:
                loz1j = loz1j[16:]
            self.external[loz1j_addr : loz1j_addr + loz1j_size] = loz1j

        # English Zelda 2
        if self.args.loz2:
            loz2_addr, loz2_size = 0x7_0000, 0x4_0000
            loz2 = self.args.loz2.read_bytes()
            # Remove the NES header
            if loz2[0] == 0x4E:
                loz2 = loz2[16:]
            self.external[loz2_addr : loz2_addr + loz2_size] = loz2

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

        # self.external.ENC_END (0x32_54A0) to the true encryped end 0x3E_0000
        # is all unused space. Extra 764,768 bytes free
        # More data at 0x3e_8000...

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
        for name, start in enumerate(BACKDROP_STARTS):
            img, consumed = decode_backdrop(self.external[start:])
            img.save(build_dir / f"backdrop_{name}.png")
            # print(hex(start + consumed))

    def _disable_save_encryption(self):
        printi("Skip the in-game save encryption.")
        self.internal.nop(0xF222, 1, expect="2:ffdd0216")
        self.internal.asm(0xF228, "add.w r2,r1,#0x10", expect="4:73f6ed6f")
        self.internal.asm(0xF22C, "sub.w r1,r8,#0x10", expect="4:d7270cd6")

        # Skip LA save state encryption
        self.internal.b(0x13ED8, 0x13F06, expect="2:fc64e0e3")

        # Skip NVRAM (system settings and vermin save) encryption
        self.internal.asm(0xB5C4, "mov r1,r2", expect="2:ce528261")
        self.internal.nop(0xB5C6, 1, expect="2:253ab265")
        self.internal.nop(0xB5CC, 1, expect="2:d0e27be0")

        # Skip ingame save decryption
        self.internal.asm(0xF12C, "add.w r7,r0,#0x10", expect="4:1f27706b")
        self.internal.asm(0xF130, "mov   r5,r1", expect="2:6bc88817")
        self.internal.asm(0xF132, "sub.w r6,r2,#0x10", expect="4:b4afc8ee")
        self.internal.asm(0xF136, "sub   sp,#0x10", expect="2:183bf1e8")
        self.internal.asm(0xF138, "mov   r1,r6", expect="2:c9f94aa1")
        self.internal.asm(0xF13A, "mov   r0,r7", expect="2:12d051f9")
        self.internal.replace(0xF13C, b"\xf4\xf7\xbc\xfc", expect="4:c894f933")
        self.internal.asm(0xF140, "mov   r2,r7", expect="2:183bf1e8")
        self.internal.asm(0xF142, "mov   r1,r6", expect="2:833b58f0")
        self.internal.asm(0xF144, "mov   r0,r5", expect="2:da09dbf1")
        self.internal.replace(0xF146, b"\xfc\xf7\x29\xfc", expect="4:60f2c317")
        self.internal.b(0xF14A, 0xF172, expect="2:92125114")

        # Skip LA save state decryption
        self.internal.b(0x13F52, 0x13F94, expect="2:290a7a8f")

        # Skip NVRAM (system settings and vermin save) decryption
        self.internal.asm(0xB528, "mov r7,r0", expect="2:f41fb101")
        self.internal.nop(0xB52A, 1, expect="2:aeb8096b")
        self.internal.replace(0xB54C, b"\xc0\xb1", expect="2:7fa409bc")

    def _recompress_backdrops(self):
        """Store the 11 backdrops as Mario's sleep images: palette + LZMA indices.

        backdrop_tick's tail call to the stock decode-by-index helper goes to
        backdrop_inflate (zelda.c) instead. The blobs are packed back to back (603424 -> ~491 KB), and the DTCM
        backdrop_images pointers follow them through fix_ext_refs().
        """
        printi("Recompressing the backdrop images.")
        self.internal.asm(
            0x1_642C,
            "b.w #" + hex(0xFFFFFFFE & self.internal.address("backdrop_inflate")),
            expect="4:234b079b",
        )

        blocks = []
        for start in BACKDROP_STARTS:
            img, _ = decode_backdrop(self.external[start:])
            if img.size != (320, 240):
                raise InvalidStockRomError(f"Unexpected backdrop size {img.size}.")
            palette_size = self.external[start + 4]
            palette = self.external[start + 6 : start + 6 + 2 * palette_size]
            blob = bytes(palette).ljust(512, b"\x00") + lzma_compress(img.tobytes())
            blocks.append((start, blob))
        self.ext_space.pack(BACKDROP_STARTS[0], BACKDROP_END, blocks)
        printd(
            f"    {BACKDROP_END - BACKDROP_STARTS[0]} -> "
            f"{sum(round_up_word(len(b)) for _, b in blocks)} bytes"
        )

    def _compress_in_place(self, start, size, end, reference):
        """LZMA the ``size`` bytes at ``start`` and drop the rest up to ``end``."""
        compressed_len = self.external.compress(start, size)
        self._expect_refs(reference, start, compressed_len)
        self.ext_space.drop(start + round_up_word(compressed_len), end)

    def _compact_ext(self):
        """Close the gaps in external flash; _finish_ext() ends the image.

        Everything from 0x20000 on shifts down (``ExtRegion.keep``/``drop``), so
        offsets inside a block, like LA's bank addressing off one ROM base,
        stay intact. Stock leaves 764 KB empty between the data (ENC_END) and
        the LA save states at 0x3E0000; the image now ends at the save states.
        The NVRAM banks and save slots below 0x20000 stay put.
        """
        b_w_memcpy_inflate_asm = "b.w #" + hex(
            0xFFFFFFFE & self.internal.address("memcpy_inflate")
        )
        self.ext_space.keep(0x2_0000, "NVRAM banks, save slots (fixed)")

        # Stock memcpy's these into RAM before use, so they're stored LZMA
        # compressed (in place; internal flash is kept for smaller data).
        printi("Compressing the clock graphics and the NES ROMs")
        # app_load: memcpy(0x24000000, clock graphics, 0x10000).
        self.internal.bl(0xFA1A, "memcpy_inflate", expect="4:880d2d82")
        self._compress_in_place(0x2_0000, 0x1_0000, 0x3_0000, 0x1_0660)
        # loz1_load: memcpy(0x30000000, LoZ1 EN ROM, 0x20000).
        self.internal.bl(0xF528, "memcpy_inflate", expect="4:f4b61f7f")
        self._compress_in_place(0x3_0000, 0x2_0000, 0x5_0000, 0xFD00)
        # loz1j_load: memcpy(0x30000000, LoZ1 JP FDS image, 0x1e000).
        self.internal.bl(0xF5C2, "memcpy_inflate", expect="4:5f322700")
        self._compress_in_place(0x5_0000, 0x1_E000, 0x7_0000, 0xFD14)
        # The helper loz2_load and loz2j_load share: memcpy(0x30000000, r1, r2).
        self.internal.asm(0xF702, b_w_memcpy_inflate_asm, expect="4:1ee08bc3")
        # loz2_load copies the LoZ2 EN PRG ROM and reads the CHR ROM after it in
        # place, at PRG + 0x20000; loz2_set_chr loads the CHR's own pointer.
        self.internal.bl(0xF666, "loz2_set_chr", expect="4:79f3cad4")
        self.internal.nop(0xF66A, 1, expect="2:8daa0f67")
        self._expect_refs(
            self.internal.address("LOZ2_CHR", sub_base=True), 0x9_0000, 0x2_0000
        )
        self._compress_in_place(0x7_0000, 0x2_0000, 0x9_0000, 0xFD18)
        # loz2j_load: the 0x1e000-byte FDS image.
        self._compress_in_place(0xB_0000, 0x1_E000, 0xD_0000, 0xFD1C)

        printi("Compressing and moving LoZ2 TIMER data to int")
        timer, timer_end = 0xD_0000, 0xD_2000
        compressed_len = self.external.compress(timer, timer_end - timer)
        self.internal.asm(0xF430, b_w_memcpy_inflate_asm, expect="4:625bb93e")
        self.move_to_int(timer, compressed_len, 0xFCF8)
        self.ext_space.drop(timer, timer_end)

        if self.args.no_la:
            self.ext_space.drop(0xD_2000, 0x1F_4C00)

        if self.args.no_sleep_images:
            self.ext_space.drop(BACKDROP_STARTS[0], BACKDROP_END)
        else:
            self._recompress_backdrops()

        # The overlay code holds pointers that fix_ext_refs() rewrites, so it's
        # appended uncompressed for now and compressed by _finish_ext().
        overlay = bytes(self.external[OVERLAY_START:OVERLAY_END])
        prov = writelog.provenance(self.external, OVERLAY_START, OVERLAY_END)
        self.ext_space.drop(OVERLAY_START, OVERLAY_END)

        stock_end = self.external.ENC_END
        self.ext_space.keep(stock_end)
        self.overlay_pos = stock_end + self.ext_space.shift
        with writelog.untracked():
            self.external[self.overlay_pos : self.overlay_pos + len(overlay)] = overlay
        writelog.set_provenance(self.external, self.overlay_pos, prov)
        base = self.external.FLASH_BASE
        self.lookup.add(base + OVERLAY_START, base + self.overlay_pos, len(overlay))

    def _finish_ext(self):
        """Compress the ITCM overlay and put the LA save states after the data.

        Runs after fix_ext_refs(): the overlay's pointers are final, and
        nothing points at the overlay except itcm_overlay_load's literals.
        """
        from .xrefs import LAYOUT

        printi("Compressing the ITCM/DTCM overlay copies")
        base = self.external.FLASH_BASE
        copies = LAYOUT[self.name]["itcm_copies"] + LAYOUT[self.name]["dtcm_copies"]
        run = {src - base: dst for src, dst, _ in copies}
        blobs = []
        for literal, offset, size in OVERLAY_COPIES:
            src = self.overlay_pos + offset - OVERLAY_START
            if self.internal.int(literal) != base + src:
                raise ValueError(f"Unexpected overlay literal at 0x{literal:X}")
            data = bytes(self.external[src : src + size])
            prov = writelog.provenance(self.external, src, src + size)
            if offset == ITCM_MENU_SRC:
                data += self.itcm_tail
                prov = np.concatenate(
                    [prov, np.full(len(self.itcm_tail), self.itcm_tail_write)]
                )
            self.ram_images.append(
                writelog.RamImage(
                    run[offset],
                    data,
                    self.external_stock[offset : offset + size],
                    prov,
                    f"overlay 0x{offset:06X}",
                )
            )
            blobs.append(lzma_compress(data))
        # itcm_overlay_load's size for the menu UI copy.
        self.internal.replace(
            0xFD58, ITCM_MENU_SIZE + len(self.itcm_tail), size=4, expect="4:25415ce7"
        )
        self.external.clear_range(self.overlay_pos, len(self.external))
        data_end = self.overlay_pos
        for (literal, offset, size), blob in zip(OVERLAY_COPIES, blobs):
            self.external[data_end : data_end + len(blob)] = blob
            self.internal.replace(literal, self.external.FLASH_BASE + data_end, size=4)
            printd(f"    0x{offset:06X}: {size} -> {len(blob)} bytes")
            self.ext_space.note(
                data_end, len(blob), self.name, f"overlay 0x{offset:06X} (LZMA)"
            )
            data_end += round_up_word(len(blob))
        if self.xip_vermin:
            data_end += round_up_word(self.xip_vermin.write(data_end))
        # The first 4 copies are bl memcpy, the last a tail call.
        self.internal.bl(0xF7A2, "memcpy_inflate", expect="4:d2879707")
        self.internal.bl(0xF7B2, "memcpy_inflate", expect="4:1a5e1d0f")
        self.internal.bl(0xF7C2, "memcpy_inflate", expect="4:c63b76ba")
        self.internal.bl(0xF7D2, "memcpy_inflate", expect="4:0ee2fcb2")
        self.internal.asm(
            0xF7E6,
            "b.w #" + hex(0xFFFFFFFE & self.internal.address("memcpy_inflate")),
            expect="4:ac7f82fd",
        )
        self.external.ENC_END = data_end

        # LA save states: 4 x 32 KB (one per language) at 0x3E0000 + lang *
        # 0x8000, erased as 32 KB blocks by factory_reset_erase_saves, so the
        # region stays 32 KB aligned.
        if self.args.no_la:
            end = round_up_page(data_end)
            # Nothing to erase; repeat the last stock save block (0x18000).
            la_blocks = [0x1_8000] * 4
        else:
            la_base = (data_end + 0x7FFF) & ~0x7FFF
            end = la_base + 0x2_0000
            la_blocks = [la_base + i * 0x8000 for i in range(4)]
            self.external.set_range(la_base, end, b"\xFF")
            self.ext_space.note(la_base, end - la_base, self.name, "LA save states")
            printi(f"Moving the LA save states to 0x{la_base:06X}")
            # la_save_state_write and la_save_state_read: 0x3e0000 + lang << 15
            self.internal.asm(
                0x1_3ED2, f"add.w r5, r5, #{hex(la_base)}", expect="4:2fae8900"
            )
            self.internal.asm(
                0x1_3F3E, f"add.w r0, r0, #{hex(la_base)}", expect="4:681a8dbd"
            )
        # save_erase_table entries 9-12.
        self.internal.replace(
            0x300,
            b"".join(x.to_bytes(4, "little") for x in la_blocks),
            expect="16:c1ea59b1",
        )

        # Like stock, the region covers the data and stops before the LA save states.
        printi("Updating end of OTFDEC region")
        self.internal.replace(
            0x1_659C,
            self.external.FLASH_BASE + round_up_page(data_end) - 1,
            size=4,
            expect="4:db3e36fa",
        )
        del self.external[end:]

        # MX_OCTOSPI1_Init sets DeviceSize to 2^22 (stock's 4 MB chip), and the
        # OSPI rejects addresses past it (GWemu doesn't). Past 4 MB widen it.
        device_size = max(22, (len(self.external) - 1).bit_length())
        if device_size > 22:
            printi(f"Setting OSPI DeviceSize to 2^{device_size}")
            self.internal.asm(
                0x1328E, f"movs r0, #{device_size}", size=2, expect="2:662f87e0"
            )

    def _erase_savedata(self):
        printi("Erase the NVRAM banks and save slots.")
        self.external.set_range(0x0000, 0x12000, b"\xFF")

    def patch(self):
        self.itcm_tail = bytearray()  # _itcm_veneer()'s veneers
        self.itcm_tail_write = -1  # Their writelog index.
        self.overlay_pos = OVERLAY_START  # Until _compact_ext() moves it.

        if self.args.relocate_stock:
            from .relocate import Relocation

            printi(f"Relocate the stock firmware to 0x{self.args.relocate_stock:08X}.")
            self.relocation = Relocation(
                self,
                0x0800_0000,
                self.STOCK_RELOCATE_END,
                self.args.relocate_stock,
                keep_vector_table=not self.amalgam,
            )

        self._dump_roms()
        self._dump_backdrops()

        if False:
            self._erase_roms()

        self._flash_roms()

        self._erase_savedata()

        if self.args.debug:
            # Override fault handlers for easier debugging via gdb.
            printi("Overriding handlers for debugging.")
            self.internal.replace(0x8, "NMI_Handler", expect="4:c90a560c")
            self.internal.replace(0xC, "HardFault_Handler", expect="4:a10a10d5")

        from .tileset import bytes_to_tilemap

        _ = bytes_to_tilemap(self.external[0x20000:0x30000])

        self._disable_save_encryption()

        if self.amalgam:
            # Mario's vector table boots the amalgam; our bootloader boots
            # Zelda through its own (relocated) table, so it keeps its stock
            # Reset_Handler. SystemInit's VTOR (0x08000000, an immediate with
            # no room for Zelda's address) is set by the bootloader instead.
            printi("Leave VTOR to the bootloader.")
            self.internal.nop(0x1_AE88, 1, expect="2:bc46897c")
        else:
            printi("Invoke custom bootloader prior to calling stock Reset_Handler.")
            self.internal.replace(0x4, "bootloader", expect="4:0138bfeb")

        printi("Intercept button presses for macros.")
        self.internal.bl(0xFE54, "read_buttons", expect="4:4c010230")

        # After a reset (anything but a power-button wake from standby), main
        # powers down and waits for the power button. Boot like that wake
        # does: power_on_init, then the main loop.
        printi("Boot after a reset instead of waiting for the power button.")
        self.internal.bl(
            0x1063C, self.internal.sym("power_on_init"), expect="4:094801b7"
        )
        self.internal.b(0x10640, 0x105EE, expect="2:3de528a1")

        printi("Mute audio on first boot.")
        # Fresh nvram is dirty, so the settings aren't loaded from it: the init
        # at 0x08018178 zeroes the struct (0x2001a7bc), then sets +21, volume
        # (+32) and brightness (+22) to 7; keep the volume at 0.
        self.internal.nop(0x1_81A8, 2, expect="4:709be8de")
        # nvram_init's default for id 7 (brightness | volume << 16), as on Mario,
        # so a commit before the settings are first saved doesn't bring 7 back.
        # Bit 2 is the TICK setting (set: off), so it starts on.
        self.internal.asm(0xB6D8, "mov.w r1, #0", expect="4:42e9afc9")

        self._second_beep_setting()

        if not self.args.encrypt:
            printi("Disable OTFDEC (external flash stays unencrypted).")
            self.internal.nop(0x16536, 2, expect="4:3c5c53f3")
            self.internal.nop(0x1653A, 1, expect="2:fdbf91e5")
            self.internal.nop(0x1653C, 1, expect="2:f588a917")

        if self.args.no_la:
            printi("Removing Link's Awakening (All Languages)")
            self.external[0x315B54] = 0x00  # Ignore LA EN menu selection
            self.external[0x315B58] = 0x00  # Ignore LA FR menu selection
            self.external[0x315B5C] = 0x00  # Ignore LA DE menu selection
            self.external[0x315B60] = 0x00  # Ignore LA JP menu selection
            # The LA ROM base (la_load): nothing can launch LA any more.
            self.internal.replace(0x1_4418, b"\x00" * 4, expect="4:5ca874f5")
            self._game_select_drop_la_row()

        if self.args.no_sleep_images:
            printi("Remove the sleep backdrops.")
            # backdrop_tick's "no backdrop" path (g_backdrop_index < 0): clear
            # the frame instead of decoding backdrop_images[index].
            self.internal.b(0x1_6426, 0x1_6430, expect="2:eb4a57f1")
            self.rwdata_erase(BACKDROP_STARTS[0], BACKDROP_END - BACKDROP_STARTS[0])

        if self.args.no_factory_test:
            printi("Remove the factory CHARGE AND KEY TEST (g_app_id 1).")
            # game_reload: never enter test mode (app 1) on boot_arg 0xffff00ff.
            self.internal.b(0xFC1E, 0xFC26, expect="2:823b923f")
            # Its static constructor -> is_usb_connected's `bx lr`.
            ctor_entry, bx_lr = 0x1_B0C8, 0x1_68B0
            self.internal.replace(
                ctor_entry,
                ((bx_lr | 1) - ctor_entry) & 0xFFFF_FFFF,
                size=4,
                expect="4:bc8a52b1",
            )
            # main_loop_tick's calls into it, each guarded by g_app_id == 1.
            calls = {
                0x1_0140: "4:426237a7",  # input
                0x1_0264: "4:cc4fd1a1",  # tick and draw
                0x1_0456: "4:daa7a0a5",  # backlight
                0x1_0496: "4:a954d95a",  # speaker-test audio (`bleq` in an IT block)
            }
            for call, expect in calls.items():
                self.internal.nop(call, 2, expect=expect)
            # Thunks, constructor, test pages, its audio and literal pools.
            self.free_int(0x1_68D8, 0x1_8154)
            # Helpers only the test used: text drawing, printf, RTC alarm.
            self.free_int(0x0_831E, 0x0_835E)
            self.free_int(0x0_837C, 0x0_8484)
            self.free_int(0x0_84A2, 0x0_8754)
            self.free_int(0x0_8764, 0x0_8790)
            self.free_int(0x0_A5C8, 0x0_A604)
            self.free_int(0x0_DD80, 0x0_E284)
            self.free_int(0x1_0CE4, 0x1_0DCC)
            self.free_int(0x1_290C, 0x1_2972)

        # The GIF decoder (decode-by-index helper at 0x08019f36 and gif_decode)
        # is only reached from backdrop_tick's tail call at 0x0801642c, which
        # either goes to backdrop_inflate or is skipped without backdrops.
        self.free_int(0x1_9CA4, 0x1_9F58)

        if self.args.xip_minigame:
            printi("Run Vermin from external flash.")
            self.xip_vermin = XipUnit(self, *VERMIN_UNIT, "Vermin code (XIP)")

        self._compact_ext()

        self.fix_ext_refs()
        if self.args.game_select_dual_boot:
            self._game_select_dual_boot()
        if self.relocation:
            # Before _finish_ext() compresses the ITCM overlay.
            self.relocation.fix_ram_images()

        self._finish_ext()

        # Compress, insert, and reference the modified rwdata
        rwdata_len = self.internal.rwdata.write_table_and_data(
            0x1B070, data_offset=self.int_space.pos
        )
        self.int_space.append(rwdata_len, self.name, "rwdata (LZMA)")
