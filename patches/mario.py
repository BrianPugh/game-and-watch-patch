from pathlib import Path

from PIL import Image

import patches

from .compression import lzma_compress
from .exception import BadImageError, InvalidStockRomError
from .firmware import Device, ExtFirmware, Firmware, IntFirmware
from .thumb_asm import ThumbAssemblyError, encode_modified_immediate
from .tileset import (
    bytes_to_tilemap,
    decode_backdrop,
    png_to_icon_4bpp,
    render_label_icon,
    tilemap_to_bytes,
)
from .utils import (
    fds_remove_crc_gaps,
    printd,
    printe,
    printi,
    round_up_page,
    round_up_word,
    seconds_to_frames,
)

build_dir = Path("build")  # TODO: expose this properly or put in better location


def _is_immediate(value):
    """Whether ``mov.w`` can load ``value`` (a Thumb modified immediate)."""
    try:
        encode_modified_immediate(value)
    except ThumbAssemblyError:
        return False
    return True


class MarioGnW(Device, name="mario"):
    # Mario's external data is packed word by word.
    EXT_SHIFT_ALIGN = 4
    # --relocate-stock moves everything below the init table, vector table
    # included.
    STOCK_RELOCATE_END = 0x0801_807C

    class Int(IntFirmware):
        STOCK_ROM_SHA1_HASH = "efa04c387ad7b40549e15799b471a6e1cd234c76"

        # Note: this isn't the ACTUAL Stock ROM end, this is actually
        # pointing to where some rwdata is, but this data will be relocated
        # and compressed. This variable is used in the linker scripts as to
        # where to start putting novel code.
        STOCK_ROM_END = 0x18100
        KEY_OFFSET = 0x106F4
        NONCE_OFFSET = 0x106E4
        RWDATA_OFFSET = 0x180A4
        RWDATA_LEN = 36
        RWDATA_ITCM_IDX = 0
        RWDATA_DTCM_IDX = 1
        # Stock functions our C code calls, as Thumb addresses. They become
        # absolute symbols (generated build/stock_symbols.s) so the ELF records
        # each use as a relocation, which the patcher needs to relocate stock code.

    class Ext(ExtFirmware):
        STOCK_ROM_SHA1_HASH = "eea70bb171afece163fb4b293c5364ddb90637ae"
        ENC_END = 0xF_E000

        def _verify(self):
            h = self.hash(self[:-8192])
            if h != self.STOCK_ROM_SHA1_HASH:
                raise InvalidStockRomError

    class FreeMemory(Firmware):
        FLASH_BASE = 0x240F2124
        FLASH_LEN = 0x24100000 - FLASH_BASE

    def add_arguments(self, parser):
        group = parser.add_argument_group("Timeout patches")

        mgroup = group.add_mutually_exclusive_group()
        mgroup.add_argument(
            "--disable-sleep", action="store_true", help="Disables sleep timer"
        )
        mgroup.add_argument(
            "--sleep-time",
            type=float,
            default=None,
            help="Go to sleep after this many seconds of inactivity.. "
            "Valid range: [1, 1092]",
        )
        group.add_argument(
            "--sleep-on-usb",
            action="store_true",
            help="Allow sleeping while USB is connected (stock never sleeps on USB power).",
        )

        group.add_argument(
            "--hard-reset-time",
            type=float,
            default=None,
            help="Hold power button for this many seconds to perform hard reset.",
        )
        group.add_argument(
            "--mario-song-time",
            type=float,
            default=None,
            help="Hold the A button for this many seconds on the time "
            "screen to launch the mario drawing song easter egg.",
        )

        group = parser.add_argument_group("Game-select menu")
        group.add_argument(
            "--game-select-dual-boot",
            action="store_true",
            help="Add an entry to the GAME menu that launches the dual-boot target "
            "(same as LEFT+GAME). Only shown if a valid target is flashed.",
        )
        mgroup = group.add_mutually_exclusive_group()
        mgroup.add_argument(
            "--game-select-dual-boot-icon",
            type=Path,
            default=Path(__file__).parent / "assets" / "retro_go_icon.png",
            help="96x48 image for the dual-boot menu entry; colors are mapped to the "
            "nearest menu palette color. Defaults to a Retro-Go logo.",
        )
        mgroup.add_argument(
            "--game-select-dual-boot-label",
            type=str,
            default=None,
            help="Instead of an image, draw this text on the dual-boot menu entry's icon.",
        )

        group = parser.add_argument_group("ROM Hacks and Graphical Mods")
        group.add_argument(
            "--smb1",
            type=Path,
            default="build/smb1.nes",
            help="Override SMB1 ROM with your own file.",
        )
        mgroup = group.add_mutually_exclusive_group()
        mgroup.add_argument(
            "--smb1-graphics",
            nargs="*",
            default=[],
            type=Path,
            help="ROM hacks where just the graphical assets will be used.",
        )
        mgroup.add_argument(
            "--smb1-graphics-glob",
            action="store_true",
            help='Add all IPS files from the "ips/" folder',
        )

        mgroup = group.add_mutually_exclusive_group()
        mgroup.add_argument(
            "--clock-tileset",
            type=Path,
            default=None,
            help="Override the clock tileset",
        )
        mgroup.add_argument(
            "--clock-tileset-index",
            type=Path,
            default=None,
            help="Override the clock tileset",
        )
        # group.add_argument(
        #    "--iconset",
        #    type=Path,
        #    default=Path("build/iconset.png"),
        #    help="Override the iconset",
        # )

        group = parser.add_argument_group("Low level flash savings flags")
        group.add_argument(
            "--no-save",
            action="store_true",
            help="Don't use up 2 pages (8192 bytes) of extflash for non-volatile saves. "
            "High scores and brightness/volume configurations will NOT survive homebrew launches.",
        )
        group.add_argument("--no-smb2", action="store_true", help="Remove SMB2 rom.")
        group.add_argument(
            "--no-mario-song",
            action="store_true",
            help="Remove the mario song easter egg (~540KB of external data, ~2.7KB of internal code).",
        )
        group.add_argument(
            "--no-sleep-images",
            action="store_true",
            help="Remove the 5 sleeping images.",
        )
        group.add_argument(
            "--no-factory-test",
            action="store_true",
            help="Remove the factory CHARGE AND KEY TEST screen (~5KB of internal code).",
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

        group = parser.add_argument_group("High level flash savings flags")
        group.add_argument(
            "--slim",
            action="store_true",
            help="Remove mario song and sleeping images from extflash.",
        )
        group.add_argument(
            "--clock-only",
            action="store_true",
            help="Everything in --slim plus remove SMB2.",
        )
        group.add_argument(
            "--internal-only",
            action="store_true",
            help="Configuration so no external flash is used.",
        )

    def set_args(self, args, parser):
        self.args = args

        ############
        # Validate #
        ############
        if self.args.sleep_time and (
            self.args.sleep_time < 1 or self.args.sleep_time > 1092
        ):
            parser.error("--sleep-time must be in range [1, 1092]")
        if self.args.mario_song_time and (
            self.args.mario_song_time < 1 or self.args.mario_song_time > 1092
        ):
            parser.error("--mario-song-time must be in range [1, 1092]")

        if len(self.args.smb1_graphics) > 8:
            parser.error("A maximum of 8 SMB1 graphics mods can be specified.")

        if self.args.smb1_graphics_glob:
            ips_folder = Path("ips")
            self.args.smb1_graphics = list(ips_folder.glob("*.ips"))
            self.args.smb1_graphics.extend(list(ips_folder.glob("*.IPS")))

        if self.args.internal_only:
            self.args.slim = True
            self.args.extended = True
            self.args.no_save = True
            if self.args.sd_bootloader:
                self.args.no_smb2 = True

        if self.args.clock_only:
            self.args.slim = True
            self.args.no_smb2 = True

        if self.args.slim:
            self.args.no_mario_song = True
            self.args.no_sleep_images = True
            self.args.no_factory_test = True

    def _tick_setting(self):
        """TICK ON/OFF: a 4th row in the clock's PAUSE/SET overlay.

        The setting is bit 0 of nvram id 5, which stock never uses. See
        tick_sound/tick_input/tick_draw in mario.c.
        """
        printi("Add a TICK ON/OFF row to the clock settings overlay.")

        def b_w(symbol):
            return "b.w #" + hex(0xFFFFFFFE & self.internal.address(symbol))

        # clock_tick_sound queues SMB1's noise sfx into $FD once a second; its
        # only call goes through tick_sound.
        self.internal.bl(0xED3A, "tick_sound", expect="4:16ec3ee5")
        # settings_clock_handle_input: 4 rows; row 3 goes to tick_input (rows
        # past 2 used to reset the cursor to 0 and fall into the volume row).
        self.internal.asm(0xC8E8, "movs r1, #4", expect="2:269d5162")
        self.internal.asm(0xC904, b_w("mario_settings_row_input"), expect="4:10f710e6")
        # settings_clock_draw: the panel grows from 0x70 to 0x90 lines and
        # starts 16 lines higher (y 0x30 instead of 0x40), so the rows, which
        # are placed relative to it, keep 16-line margins.
        self.internal.asm(0xCD82, "movs r1, #0x90", expect="2:228f7b57")  # dirty rect h
        self.internal.asm(0xCD8C, "movs r2, #0x30", expect="2:64c01c1e")  # dirty rect y
        self.internal.asm(0xCD94, "movw r0, #0x3c28", expect="4:66937e07")  # (40, 48)
        self.internal.asm(0xCD9C, "movs r2, #0x90", expect="2:bb862aed")  # panel h
        # Its last tail call (after the pop) draws the SET TIME text.
        self.internal.asm(0xCEA2, b_w("tick_draw"), expect="4:3b91bb65")

    def patch(self):
        if self.args.relocate_stock:
            from .relocate import Relocation

            printi(f"Relocate the stock firmware to 0x{self.args.relocate_stock:08X}.")
            self.relocation = Relocation(
                self, 0x0800_0000, self.STOCK_RELOCATE_END, self.args.relocate_stock
            )

        printi("Invoke custom bootloader prior to calling stock Reset_Handler.")
        self.internal.replace(0x4, "bootloader", expect="4:c32823e6")

        printi("Intercept button presses for macros.")
        self.internal.bl(0x6B52, "read_buttons", expect="4:8d256c72")

        # After a reset (anything but a power-button wake from standby), main
        # powers down and waits for the power button. Boot like that wake
        # does: power_on_init, then the main loop.
        printi("Boot after a reset instead of waiting for the power button.")
        self.internal.bl(
            0x7312, self.internal.sym("power_on_init"), expect="4:743ff5f2"
        )
        self.internal.b(0x7316, 0x72C4, expect="2:3de528a1")

        if self.args.no_factory_test:
            printi("Remove the factory CHARGE AND KEY TEST (g_gnw_mode 0x01).")
            # game_reload: never enter test mode on the magic boot value.
            self.internal.b(0x66D2, 0x66DA, expect="2:823b923f")
            # Its static constructor -> an existing `bx lr`.
            ctor_entry, bx_lr = 0x1_7E08, 0x659A
            self.internal.replace(
                ctor_entry,
                ((bx_lr | 1) - ctor_entry) & 0xFFFF_FFFF,
                size=4,
                expect="4:7458e291",
            )
            # main_loop_tick calls, each guarded by g_gnw_mode == 1.
            calls = {
                0x6CC4: "4:2e84db16",
                0x6DBC: "4:bc3188c7",
                0x70E4: "4:4712a6f5",
                0x711C: "4:d2968257",
            }
            for call, expect in calls.items():
                self.internal.nop(call, 2, expect=expect)
            # Thunks, constructor, body and literal pools.
            self.free_int(0x1_1A94, 0x1_2EE4)

        if self.args.no_mario_song:
            printi("Remove the mario song code (g_gnw_mode 0x04).")
            # Mode 4 is never entered (see the 0x6FC8 patch below); these are
            # main_loop_tick's calls, each guarded by (g_gnw_mode & 0xC6) == 4.
            calls = {
                0x6980: "4:d4d038db",
                0x6DAE: "4:02222f90",
                0x7008: "4:ccbd1f14",
                0x7112: "4:29521448",
            }
            for call, expect in calls.items():
                self.internal.nop(call, 2, expect=expect)
            # Its static constructor -> an existing `bx lr`.
            ctor_entry, bx_lr = 0x1_7E04, 0x659A
            self.internal.replace(
                ctor_entry,
                ((bx_lr | 1) - ctor_entry) & 0xFFFF_FFFF,
                size=4,
                expect="4:53d049ab",
            )
            if not self.args.no_factory_test:
                # The factory test stops the song; nothing to stop now.
                self.internal.nop(0x1_1D20, 2, expect="4:5793f5ae")
            # Constructor, body and literal pools.
            self.free_int(0x1_0FAC, 0x1_1A24)

        if self.args.no_mario_song and self.args.no_factory_test:
            printi("Remove the drawing library only used by the song and factory test.")
            ctor_entry, bx_lr = 0x1_7E10, 0x659A
            self.internal.replace(
                ctor_entry,
                ((bx_lr | 1) - ctor_entry) & 0xFFFF_FFFF,
                size=4,
                expect="4:33ea1c5f",
            )
            # Constructor, primitives and the text printf.
            self.free_int(0x1_6F30, 0x1_7394)
            # Its vsnprintf and helpers, down to memchr.
            self.free_int(0x1_73EA, 0x1_7A30)

        if self.args.game_select_dual_boot:
            printi("Add dual-boot entry to the game-select menu.")
            # Each hooked function has exactly one call site.
            self.internal.bl(0xC894, "mario_game_select_prev", expect="4:f87db677")
            self.internal.bl(0xC89C, "mario_game_select_next", expect="4:f2501abf")
            self.internal.bl(
                0xCB18, "mario_game_select_handle_input", expect="4:fa0dc437"
            )
            self.internal.bl(0xD236, "mario_game_select_draw", expect="4:37ecc1ee")
            if self.args.game_select_dual_boot_label:
                icon = render_label_icon(self.args.game_select_dual_boot_label)
            else:
                icon = png_to_icon_4bpp(self.args.game_select_dual_boot_icon)
            self.internal.replace(
                self.internal.address(
                    "MARIO_GAME_SELECT_DUAL_BOOT_ICON", sub_base=True
                ),
                icon,
            )

        printi("Mute clock audio on first boot.")
        self.internal.asm(0x49E0, "mov.w r1, #0x00000", expect="4:42e9afc9")

        self._tick_setting()

        if self.args.debug:
            # Override fault handlers for easier debugging via gdb.
            printi("Overriding handlers for debugging.")
            self.internal.replace(0x8, "NMI_Handler", expect="4:2e7195dd")
            self.internal.replace(0xC, "HardFault_Handler", expect="4:631a8cd8")

        if self.args.hard_reset_time:
            hard_reset_time_ms = int(round(self.args.hard_reset_time * 1000))
            printi(
                f"Hold power button for {hard_reset_time_ms} milliseconds to perform hard reset."
            )
            self.internal.asm(
                0x9CEE, f"movw r1, #{hard_reset_time_ms}", expect="4:16285583"
            )

        if self.args.sleep_time:
            printi(f"Setting sleep time to {self.args.sleep_time} seconds.")
            sleep_time_frames = seconds_to_frames(self.args.sleep_time)
            self.internal.asm(
                0x6C3C, f"movw r2, #{sleep_time_frames}", expect="4:ab3637df"
            )

        if self.args.disable_sleep:
            printi("Disable sleep timer")
            self.internal.replace(0x6C40, 0x91, size=1, expect="1:220d7cc9")

        if self.args.sleep_on_usb:
            printi("Allow sleeping while USB is connected.")
            # main_loop_tick's idle timer reset (0x6BEE) and wake-from-sleep
            # (0x6BA6) both fire when is_usb_connected() or there's input.
            for call, expect in {0x6BA6: "4:93e9491c", 0x6BEE: "4:defc4f62"}.items():
                self.internal.asm(call, "movs r0, #0; nop", size=4, expect=expect)

        # Without the song (--no-mario-song), 0x6FC8 just skips its start.
        if self.args.mario_song_time and not self.args.no_mario_song:
            printi(f"Setting Mario Song time to {self.args.mario_song_time} seconds.")
            mario_song_frames = seconds_to_frames(self.args.mario_song_time)
            # Stock "cmp.w r0, #300; blt 0x6fe4" only takes modified immediates
            # (no odd seconds), so load the frame count into r1, which is dead
            # here (the song-start path zeroes it; the loop head rewrites it).
            # The 2 extra bytes come from shifting the song-start path down and
            # shortening its "orr.w r3, r3, #4" to "adds r3, #4": bit 2 of
            # (mode & 0xc038) is clear, and the flags die at 0x6fee.
            song_start = bytes(self.internal[0x6FCA:0x6FDC])
            self.internal.asm(
                0x6FC4,
                f"movw r1, #{mario_song_frames}; cmp r0, r1; blt 0x20",
                size=8,
                # Through the "orr.w r3, r3, #4" the song-start path ends with.
                expect="28:c1b840c4",
            )
            self.internal[0x6FCC:0x6FDE] = song_start
            self.internal.asm(0x6FDE, "adds r3, #4", size=2)

        if not self.args.encrypt:
            # Disable OTFDEC
            self.internal.nop(0x10688, 2, expect="4:83fb638f")
            self.internal.nop(0x1068E, 1, expect="2:f588a917")

        # Dump the tileset
        tileset_addr, tileset_size = 0x9_8B84, 0x1_0000
        palette_addr = 0xB_EC68
        palette = self.external[palette_addr : palette_addr + 320]
        tileset_bytes = self.external[tileset_addr : tileset_addr + tileset_size]
        tileset = bytes_to_tilemap(tileset_bytes, palette=palette)
        tileset.save(build_dir / "tileset.png")
        tileset_index = bytes_to_tilemap(tileset_bytes)
        tileset_index.save(build_dir / "tileset_index.png")

        # Override tileset
        if self.args.clock_tileset:
            with Image.open(self.args.clock_tileset) as tileset:
                if tileset.height != 256 or tileset.width != 256:
                    raise BadImageError(
                        "Clock tileset image must have height=256, width=256"
                    )
                tileset = tileset.convert("RGB")
                if tileset.getpixel((255, 255))[:3] != (95, 115, 255):
                    raise BadImageError(
                        "Clock tileset image color is corrupt. Possibly due to some gamma issue."
                    )
                self.external[
                    tileset_addr : tileset_addr + tileset_size
                ] = tilemap_to_bytes(tileset, palette)

        # Dump the iconset
        iconset_addr, iconset_size = 0xAACE4, 0x3F00
        palette_addr = 0xB_EC68
        palette = self.external[palette_addr : palette_addr + 320]
        iconset = bytes_to_tilemap(
            self.external[iconset_addr : iconset_addr + iconset_size],
            palette=palette,
            bpp=4,
        )
        iconset.save(build_dir / "iconset.png")

        # Override iconset
        # with Image.open(self.args.iconset) as iconset:
        #    if iconset.height != 128 or iconset.width !=256:
        #        raise BadImageError("Iconset image must have height=128, width=256")
        #    iconset = iconset.convert("RGB")
        #    if iconset.getpixel((255, 127))[:3] != (95, 115, 255):
        #        raise BadImageError("Iconset image color is corrupt. Possibly due to some gamma issue.")
        #    self.external[iconset_addr : iconset_addr + iconset_size] = \
        #        tilemap_to_bytes(iconset, palette, bpp=4)[:iconset_size]

        # Dump BALL logo
        # ball_logo_addr, ball_logo_size = 0x1_13CC, 768
        # palette_addr = 0xB_EC68
        # palette = self.external[palette_addr : palette_addr + 320]
        # ball_logo = bytes_to_tilemap(
        #    self.external[ball_logo_addr : ball_logo_addr + ball_logo_size],
        #    palette=palette,
        #    width=128,
        #    bpp=2,
        # )
        # ball_logo.save(build_dir / "ball_logo.png")

        if self.args.smb1_graphics:
            printi("Intercept prepare_clock_rom")
            self.internal.bl(0x690E, "prepare_clock_rom", expect="4:0ef23293")
            self.internal.nop(0x1_0EF0, 2, expect="4:699f7905")

            table = self.internal.address("SMB1_GRAPHIC_MODS", sub_base=True)
            for file_path in self.args.smb1_graphics:
                if file_path.suffix.lower() == ".nes":
                    rom = file_path.read_bytes()
                    if len(rom) == 40976:
                        # Remove the NES header
                        rom = rom[16:]
                    assert len(rom) == 40960
                    graphics = rom[0x8000:0x9EC0]
                    graphics_compressed = lzma_compress(graphics)
                    loc = self.move_to_int(
                        graphics_compressed, len(graphics_compressed), None
                    )
                    loc += self.internal.FLASH_BASE
                elif file_path.suffix.lower() == ".ips":
                    patch = file_path.read_bytes()
                    patch = patches.ips.strip_header(patch)
                    loc = self.move_to_int(patch, len(patch), None)
                    loc += self.internal.FLASH_BASE
                else:
                    raise ValueError(
                        f"Don't know how to handle extension for {file_path}."
                    )
                # Update the SMB1_GRAPHIC_MODS table
                self.internal.replace(table, loc, size=4)
                self.int_pointers.append(table)
                table += 4

        printd("Compressing and moving stuff stuff to internal firmware.")
        compressed_len = self.external.compress(
            0x0, 7772
        )  # Dst expects only 7772 bytes, not 7776
        self.internal.bl(0x665C, "memcpy_inflate", expect="4:919d5894")
        self.move_ext(0x0, compressed_len, 0x7204)
        # Note: the 4 bytes between 7772 and 7776 is padding.
        self.ext_space.drop(0x0, 7776)

        # SMB1 ROM (plus loading custom ROM)
        printd("Compressing and moving SMB1 ROM to compressed_memory.")
        smb1_addr, smb1_size = 0x1E60, 40960
        # Adding the header for patching convenience.
        (build_dir / "smb1.nes").write_bytes(
            b"NES\x1a\x02\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00"
            + self.external[smb1_addr : smb1_addr + smb1_size]
        )
        smb1 = self.args.smb1.read_bytes()
        if len(smb1) == 40976:
            # Remove the NES header
            smb1 = smb1[16:]
        if len(smb1) != smb1_size:
            raise ValueError(f"Unknown length {len(smb1)} of file {self.args.smb1}")
        self.external[smb1_addr : smb1_addr + smb1_size] = smb1
        patch_smb1_refr = self.internal.address("SMB1_ROM", sub_base=True)
        self.move_to_compressed_memory(
            smb1_addr, smb1_size, [0x7368, 0x10954, 0x7218, patch_smb1_refr]
        )

        # Clock scene tile layers; only the scene table (below) points here.
        self.move_to_compressed_memory(0xBE60, 11620, None)

        # Starting here are BALL references
        self.move_to_compressed_memory(0xEBC4, 528, 0x4154)

        self.move_to_compressed_memory(0xEDD4, 100, 0x4570)

        references = {
            0xEE38: 0x4514,
            0xEE78: 0x4518,
            0xEEB8: 0x4520,
            0xEEF8: 0x4524,
        }
        for external, internal in references.items():
            self.move_to_compressed_memory(external, 64, internal)

        references = [
            0x2AC,
            0x2B0,
            0x2B4,
            0x2B8,
            0x2BC,
            0x2C0,
            0x2C4,
            0x2C8,
            0x2CC,
            0x2D0,
        ]
        self.move_to_compressed_memory(0xEF38, 128 * 10, references)

        self.move_to_compressed_memory(0xF438, 96, 0x456C)
        self.move_to_compressed_memory(0xF498, 180, 0x43F8)

        # This is the first thing passed into the drawing engine.
        self.move_to_compressed_memory(0xF54C, 1100, 0x43FC)
        self.move_to_compressed_memory(0xF998, 180, 0x4400)
        self.move_to_compressed_memory(0xFA4C, 1136, 0x4404)
        self.move_to_compressed_memory(0xFEBC, 864, 0x450C)
        self.move_to_compressed_memory(0x1_021C, 384, 0x4510)
        self.move_to_compressed_memory(0x1_039C, 384, 0x451C)
        self.move_to_compressed_memory(0x1_051C, 384, 0x4410)
        self.move_to_compressed_memory(0x1_069C, 384, 0x44F8)
        self.move_to_compressed_memory(0x1_081C, 384, 0x4500)
        self.move_to_compressed_memory(0x1_099C, 384, 0x4414)
        self.move_to_compressed_memory(0x1_0B1C, 384, 0x44FC)
        self.move_to_compressed_memory(0x1_0C9C, 384, 0x4504)
        self.move_to_compressed_memory(0x1_0E1C, 384, 0x440C)
        self.move_to_compressed_memory(0x1_0F9C, 384, 0x4408)
        self.move_to_compressed_memory(0x1_111C, 192, 0x44F4)
        self.move_to_compressed_memory(0x1_11DC, 192, 0x4508)
        self.move_to_compressed_memory(0x1_129C, 304, 0x458C)
        self.move_to_compressed_memory(
            0x1_13CC, 768, 0x4584
        )  # BALL logo tile idx tight
        self.move_to_compressed_memory(0x1_16CC, 1144, 0x4588)
        self.move_to_compressed_memory(0x1_1B44, 768, 0x4534)
        self.move_to_compressed_memory(0x1_1E44, 32, 0x455C)
        self.move_to_compressed_memory(0x1_1E64, 32, 0x4558)
        self.move_to_compressed_memory(0x1_1E84, 32, 0x4554)
        self.move_to_compressed_memory(0x1_1EA4, 32, 0x4560)
        self.move_to_compressed_memory(0x1_1EC4, 32, 0x4564)
        self.move_to_compressed_memory(0x1_1EE4, 64, 0x453C)
        self.move_to_compressed_memory(0x1_1F24, 64, 0x4530)
        self.move_to_compressed_memory(0x1_1F64, 64, 0x4540)
        self.move_to_compressed_memory(0x1_1FA4, 64, 0x4544)
        self.move_to_compressed_memory(0x1_1FE4, 64, 0x4548)
        self.move_to_compressed_memory(0x1_2024, 64, 0x454C)
        self.move_to_compressed_memory(0x1_2064, 64, 0x452C)
        self.move_to_compressed_memory(0x1_20A4, 64, 0x4550)

        self.move_to_compressed_memory(0x1_20E4, 21 * 96, 0x4574)
        self.move_to_compressed_memory(0x1_28C4, 192, 0x4578)
        self.move_to_compressed_memory(0x1_2984, 640, 0x457C)

        # This is a 320 byte palette used for BALL, but the last 160 bytes are empty
        self.move_to_compressed_memory(0x1_2C04, 320, 0x4538)

        mario_song_len = 0x85E40  # 548,416 bytes
        if self.args.no_mario_song:
            # This isn't really necessary, but we keep it here because its more explicit.
            printe("Erasing Mario Song")
            self.external.replace(0x1_2D44, b"\x00" * mario_song_len)
            self.rwdata_erase(0x1_2D44, mario_song_len)
            self.ext_space.drop(0x1_2D44, 0x1_2D44 + mario_song_len)

            self.internal.asm(0x6FC8, "b 0x1c", expect="2:fc6d56af")
        else:
            references = [
                # Banners
                0x11A00,
                0x11A00 + 4,
                0x11A00 + 8,
                0x11A00 + 12,
                0x11A00 + 16,
                0x11A00 + 20,
                0x11A00 + 24,
                # Audio
                0x1199C,
            ]
            self.move_ext(0x1_2D44, mario_song_len, references)

        # Each tile is 16x16 pixels, stored as 256 bytes in row-major form.
        # These index into one of the palettes starting at 0xbec68.
        printe("Compressing clock graphics")
        compressed_len = self.external.compress(0x9_8B84, 0x1_0000)
        self.internal.bl(0x678E, "memcpy_inflate", expect="4:2228f44e")

        printe("Moving clock graphics")
        self.move_ext(0x9_8B84, compressed_len, 0x7350)
        self.ext_space.drop(0x9_8B84, 0xA_8B84)

        # Note: the clock uses a different palette; this palette only applies
        # to ingame Super Mario Bros 1 & 2
        printe("Moving NES emulator palette.")
        self.move_to_compressed_memory(0xA_8B84, 192, 0xB720)

        # NES emulator tables, read by nes_init and the core: 6502 opcode
        # records (256 x 12 B), handler tables, a 192 x 20-B table at
        # 0x900a9ce4 (mapper init functions?) and apu_reciprocal_table.
        self.move_to_compressed_memory(0xA_8C44, 8352, 0xBC44)

        printe("Moving iconset.")
        # MODIFY THESE IF WE WANT CUSTOM GAME ICONS
        self.move_to_compressed_memory(0xA_ACE4, 16128, [0xCEA8, 0xD2F8])

        printe("Moving menu stuff (icons? meta?)")
        references = [
            0x0_D010,
            0x0_D004,
            0x0_D2D8,
            0x0_D2DC,
            0x0_D2F4,
            0x0_D2F0,
        ]
        self.move_to_compressed_memory(0xA_EBE4, 116, references)

        # Dump a playable version of SMB2
        smb2_addr, smb2_size = 0xA_EC58, 0x1_0000
        smb2_end = smb2_addr + smb2_size
        smb2 = self.external[smb2_addr:smb2_end].copy()
        smb2 = fds_remove_crc_gaps(smb2)
        (build_dir / "smb2.fds").write_bytes(smb2)

        if self.args.no_smb2:
            printe("Erasing SMB2 ROM")
            self.external.replace(
                smb2_addr,
                b"\x00" * smb2_size,
            )
            self.ext_space.drop(smb2_addr, smb2_end)

            # Replace conditional-branch with unconditional
            # TODO: this prevents hardlocking when selecting SMB2 from the menu,
            # but it's not quite right and is glitchy.
            self.internal.b(0x69FC, 0x6A8C, expect="2:dae5b803")
        else:
            printe("Compressing and moving SMB2 ROM.")
            compressed_len = self.external.compress(smb2_addr, smb2_size)
            self.internal.bl(0x6A12, "memcpy_inflate", expect="4:9760e95f")
            self.move_to_compressed_memory(smb2_addr, compressed_len, 0x7374)
            self.ext_space.drop(smb2_addr, smb2_end)  # Move by the space savings.

            # Round to nearest page so that the length can be used as an imm
            compressed_len = round_up_page(compressed_len)

            # Update the length of the compressed data (doesn't matter if its too large)
            self.internal.asm(
                0x6A0A, f"mov.w r2, #{compressed_len}", expect="4:8c95fc46"
            )
            self.internal.asm(
                0x6A1E, f"mov.w r3, #{compressed_len}", expect="4:fb92ccd0"
            )

        # "PRESS" and "BUTTON" as font tile indices, for the first-boot
        # PRESS TIME BUTTON screen (press_time_screen_draw).
        self.move_to_compressed_memory(0xBEC58, 8 * 2, 0x10964)

        printe("Moving Palettes")
        # There are 80 colors, each in BGRA format, where A is always 0
        # These are referenced by the scene table.
        self.move_to_compressed_memory(0xBEC68, 320, None)  # Day palette [0600, 1700]
        self.move_to_compressed_memory(0xBEDA8, 320, None)  # Night palette [1800, 0400)
        self.move_to_compressed_memory(
            0xBEEE8, 320, None
        )  # Underwater palette (between 1200 and 2400 at XX:30)
        # The dawn palette with 3 of its 80 colors changed. In each run of 10
        # scenes, slots 7 and 8 use it, between dawn (6) and night (9).
        self.move_to_compressed_memory(0xBF028, 320, None)
        self.move_to_compressed_memory(0xBF168, 320, None)  # Dawn palette [0500, 0600)

        # Scene headers: (width, height) in 16-px tiles, 2 x uint32 each.
        # Mostly (0x36, 0xF), a few (0x30, 0xF) and (0x20, 0xF).
        # Referenced by the scene table
        self.move_to_compressed_memory(0xBF2A8, 45 * 8, None)

        # Scripted NES pad input for Mario on the clock, one byte per step
        # (0x01 Right, 0x81 A+Right, 0xFF ends), read by clock_mario_drive.
        self.move_to_compressed_memory(0xBF410, 144, 0x1658C)

        # SCENE TABLE
        # Goes in chunks of 20 bytes (5 addresses)
        # Each scene is represented by 5 pointers (clock_scene_load):
        #    1. Header: (w, h) in tiles. h is always 15 (240 px); w is
        #       usually 54 (864 px).
        #    2-4. 3 compressed layers of w * h uint16 tiles, decoded by
        #         0x08013ee0 into 0x2000ee6c, 0x2000f4c8 and 0x2000fb24.
        #    5. Palette (copied to 0x20010554).
        # Its pointers are "ext_pointer_tables" in patches/xrefs.py.
        lookup_table_start = 0xB_F4A0
        lookup_table_end = 0xB_F838
        lookup_table_len = lookup_table_end - lookup_table_start  # 46 * 5 * 4 = 920
        self.move_to_compressed_memory(lookup_table_start, lookup_table_len, 0xDF88)

        # Clock digit tables: where each digit's bricks go in SMB1's block
        # buffer (NES RAM 0x500), read by the clock module (UNVERIFIED).
        references = [
            0xE8F8,
            0xF4EC,
            0xF4F8,
            0x10098,
            0x105B0,
        ]
        self.move_to_compressed_memory(0xBF838, 280, references)

        # Clock digit glyphs: 12 x (5 rows x 3 columns) brick cells, indexed
        # by a BCD time digit; clock_digit_shatter breaks them into particles.
        self.move_to_compressed_memory(0xBF950, 180, [0xE2E4, 0xF4FC])
        # Bytes clock_mario_drive copies into its state (UNVERIFIED).
        self.move_to_compressed_memory(0xBFA04, 8, 0x1_6590)
        self.move_to_compressed_memory(0xBFA0C, 784, 0x1_0F9C)

        # MOVE EXTERNAL FUNCTIONS
        # Their literals and the internal pointers to them (0x0800d308-0x0800d398)
        # are relocated by fix_ext_refs().
        self.move_ext(0xB_FD1C, 14244, None)

        # BALL sound samples.
        self.move_to_compressed_memory(0xC34C0, 6168, 0x43EC)
        self.move_to_compressed_memory(0xC4CD8, 2984, 0x459C)
        self.move_to_compressed_memory(0xC5880, 120, 0x4594)

        total_image_length = 193_568
        references = [
            0x1097C,
            0x1097C + 4,
            0x1097C + 8,
            0x1097C + 12,
            0x1097C + 16,
        ]
        for name, index in [
            ("mario_sleeping", 0xC_58F8),
            ("mario_juggling", 0xC_D858),
            ("bowser_sleeping", 0xD_6C78),
            ("pizza", 0xE_16F8),
            ("minions_sleeping", 0xE_C318),
        ]:
            img, _ = decode_backdrop(self.external[index:])
            img.save(build_dir / f"backdrop_{name}.png")

        # The stock GIF decoder's only caller is the tail call at 0x10938, which
        # is either hooked below or unreachable once the images are deleted.
        self.free_int(0x1_6764, 0x1_69F0)

        if self.args.no_sleep_images:
            # Images Notes:
            #    * In-between images are just zeros.
            #
            # start: 0x900C_58F8   end: 0x900C_D83F    mario sleeping
            # start: 0x900C_D858   end: 0x900D_6C65    mario juggling
            # start: 0x900D_6C78   end: 0x900E_16E2    bowser sleeping
            # start: 0x900E_16F8   end: 0x900E_C301    mario and luigi eating pizza
            # start: 0x900E_C318   end: 0x900F_4D04    minions sleeping
            #          zero_padded_end: 0x900f_4d18
            # Total Image Length: 193_568 bytes
            printe("Deleting sleeping images.")
            self.external.replace(0xC58F8, b"\x00" * total_image_length)
            for reference in references:
                self.internal.replace(reference, b"\x00" * 4)  # Erase image references
            self.ext_space.drop(0xC58F8, 0xC58F8 + total_image_length)
        else:
            printe("Recompressing sleeping images.")
            # Replace the stock GIF decoder (tail-called at 0x10938) with ours.
            self.internal.asm(
                0x10938,
                "b.w #"
                + hex(0xFFFFFFFE & self.internal.address("sleep_image_inflate")),
                expect="4:4cf10da3",
            )
            # Each image's literal pool reference, keyed by its ext offset.
            literals = {
                self.internal.int(ref) - self.external.FLASH_BASE: ref
                for ref in references
            }
            starts = sorted(literals)
            ends = starts[1:] + [0xC58F8 + total_image_length]
            for start, end in zip(starts, ends):
                # LZMA over the raw 8-bit indices is ~23% smaller than the LZW.
                img, _ = decode_backdrop(self.external[start:])
                if img.size != (320, 240):
                    raise InvalidStockRomError(
                        f"Unexpected sleep image size {img.size}."
                    )
                palette_size = self.external[start + 4]
                palette = self.external[start + 6 : start + 6 + 2 * palette_size]
                blob = bytes(palette).ljust(512, b"\x00") + lzma_compress(img.tobytes())
                blob = blob.ljust(round_up_word(len(blob)), b"\x00")
                print(f"    {end - start} -> {len(blob)} bytes")
                # Placing each blob at its image's original offset lets the
                # literal pool reference relocate through the normal lookup.
                self.external.clear_range(start, end)
                self.external[start : start + len(blob)] = blob
                self.move_ext(start, len(blob), literals[start])
                self.ext_space.drop(start, end)

        # The TIME graphic of the first-boot PRESS TIME BUTTON screen.
        self.move_to_compressed_memory(0xF4D18, 2880, 0x10960)

        # An ITCM overlay that game_reload copies with length 0 (literal at
        # 0x08007210), so it's never used.
        self.external.replace(0xF5858, b"\x00" * 34728)
        self.ext_space.drop(0xF5858, 0xF5858 + 34728)

        self.fix_ext_refs()
        if self.relocation:
            self.relocation.fix_ram_images()

        if self.sram3_space.pos:
            # Compress and copy over compressed_memory
            self.internal.rwdata.append(
                self.compressed_memory[: self.sram3_space.pos].copy(),
                self.compressed_memory.FLASH_BASE,
            )

        # Compress, insert, and reference the modified rwdata
        rwdata_len = self.internal.rwdata.write_table_and_data(
            0x17DB4, data_offset=self.int_space.pos
        )
        self.int_space.append(rwdata_len, self.name, "rwdata (LZMA)")

        # Shorten the external firmware. The NVRAM banks go at the end, after
        # the compacted data; rounding the negative shift towards zero leaves
        # them page aligned.
        ext_shift = round_up_page(self.ext_space.shift)

        if self.args.no_save:
            # Disable nvram loading
            calls = {0x495E: "4:80ad245d", 0x49A6: "4:7145c5fb", 0x49B2: "4:efc6e875"}
            for call, expect in calls.items():
                self.internal.nop(call, 2, expect=expect)
            # self.internal.b(0x4988, 0x49be)  # If you still want the first-startup "Press TIME Button" screen
            self.internal.b(
                0x4988, 0x49C0, expect="2:2871016b"
            )  # Skips Press TIME Button screen

            # Disable nvram saving
            # This just skips the body of the nvram_write_bank function
            self.internal.b(0x48BE, 0x4912, expect="2:f02c2fc0")

            ext_shift -= 8192
        else:
            # The 2 banks' flash offsets are immediates. Past 1 MB (the amalgam
            # puts Mario's data after Zelda's) they can't both be: bank 0 goes
            # at the next 4 KB step that encodes, bank 1 is 4 KB on.
            nvram = 0xFE000 + ext_shift
            if _is_immediate(nvram) and _is_immediate(nvram + 0x1000):
                code = (
                    f"ite ne; movne.w r4, #{hex(nvram + 0x1000)}; "
                    f"moveq.w r4, #{hex(nvram)}"
                )
            else:
                while not _is_immediate(nvram):
                    nvram += 0x1000
                code = f"mov.w r4, #{hex(nvram)}; it ne; addne.w r4, r4, #0x1000"
            ext_shift = nvram - 0xFE000
            printi("Update NVRAM read addresses")
            self.internal.asm(0x4856, code, expect="10:08ae9d3d")
            printi("Update NVRAM write addresses")
            self.internal.asm(0x48C0, code, expect="10:08ae9d3d")

        # Finally, set the firmware's length
        printi("Updating end of OTFDEC pointer")
        self.internal.add(0x1_06EC, ext_shift, expect="4:0e03246b")
        end = 0x10_0000 + ext_shift
        if end > len(self.external):
            self.external.extend(b"\x00" * (end - len(self.external)))
        self.external.shorten(len(self.external) - end)

        # MX_OCTOSPI1_Init sets DeviceSize to 2^20 (stock's 1 MB chip), and the
        # OSPI rejects addresses past it (GWemu doesn't). Past 1 MB (the
        # amalgam) widen it.
        device_size = max(20, (len(self.external) - 1).bit_length())
        if device_size > 20:
            printi(f"Setting OSPI DeviceSize to 2^{device_size}")
            self.internal.asm(
                0xA41E, f"movs r0, #{device_size}", size=2, expect="2:5419e562"
            )
        if not self.args.no_save:
            self.ext_space.note(0xFE000 + ext_shift, 0x2000, self.name, "NVRAM banks")
