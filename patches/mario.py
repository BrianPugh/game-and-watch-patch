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


class MarioGnW(Device, name="mario"):
    class Int(IntFirmware):
        STOCK_ROM_SHA1_HASH = "efa04c387ad7b40549e15799b471a6e1cd234c76"
        STOCK_ROM_END = 0x18100  # Used for generating linker script.
        KEY_OFFSET = 0x106F4
        NONCE_OFFSET = 0x106E4
        RWDATA_OFFSET = 0x180A4
        RWDATA_LEN = 36
        RWDATA_ITCM_IDX = 0
        RWDATA_DTCM_IDX = 1

    class Ext(ExtFirmware):
        STOCK_ROM_SHA1_HASH = "eea70bb171afece163fb4b293c5364ddb90637ae"
        ENC_LEN = 0xF_E000

        def _verify(self):
            h = self.hash(self[:-8192])
            if h != self.STOCK_ROM_SHA1_HASH:
                raise InvalidStockRomError

    class FreeMemory(Firmware):
        FLASH_BASE = 0x240F2124
        FLASH_LEN = 0x24100000 - FLASH_BASE

    def argparse(self, parser):
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
            help="Remove the mario song easter egg.",
        )
        group.add_argument(
            "--no-sleep-images",
            action="store_true",
            help="Remove the 5 sleeping images.",
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

        self.args = parser.parse_args()

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
            parser.error("--mario_song-time must be in range [1, 1092]")

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

        if self.args.clock_only:
            self.args.slim = True
            self.args.no_smb2 = True

        if self.args.slim:
            self.args.no_mario_song = True
            self.args.no_sleep_images = True

        return self.args

    def patch(self):
        printi("Invoke custom bootloader prior to calling stock Reset_Handler.")
        self.internal.replace(0x4, "bootloader")

        printi("Intercept button presses for macros.")
        self.internal.bl(0x6B52, "read_buttons")

        printi("Mute clock audio on first boot.")
        self.internal.asm(0x49E0, "mov.w r1, #0x00000")

        if self.args.debug:
            # Override fault handlers for easier debugging via gdb.
            printi("Overriding handlers for debugging.")
            self.internal.replace(0x8, "NMI_Handler")
            self.internal.replace(0xC, "HardFault_Handler")

        if self.args.hard_reset_time:
            hard_reset_time_ms = int(round(self.args.hard_reset_time * 1000))
            printi(
                f"Hold power button for {hard_reset_time_ms} milliseconds to perform hard reset."
            )
            self.internal.asm(0x9CEE, f"movw r1, #{hard_reset_time_ms}")

        if self.args.sleep_time:
            printi(f"Setting sleep time to {self.args.sleep_time} seconds.")
            sleep_time_frames = seconds_to_frames(self.args.sleep_time)
            self.internal.asm(0x6C3C, f"movw r2, #{sleep_time_frames}")

        if self.args.disable_sleep:
            printi("Disable sleep timer")
            self.internal.replace(0x6C40, 0x91, size=1)

        if self.args.mario_song_time:
            printi(f"Setting Mario Song time to {self.args.mario_song_time} seconds.")
            mario_song_frames = seconds_to_frames(self.args.mario_song_time)
            self.internal.asm(0x6FC4, f"cmp.w r0, #{mario_song_frames}")

        if not self.args.encrypt:
            # Disable OTFDEC
            self.internal.nop(0x10688, 2)
            self.internal.nop(0x1068E, 1)

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
            self.internal.bl(0x690E, "prepare_clock_rom")
            self.internal.nop(0x1_0EF0, 2)

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
                table += 4

        printd("Compressing and moving stuff stuff to internal firmware.")
        compressed_len = self.external.compress(
            0x0, 7772
        )  # Dst expects only 7772 bytes, not 7776
        self.internal.bl(0x665C, "memcpy_inflate")
        self.move_ext(0x0, compressed_len, 0x7204)
        # Note: the 4 bytes between 7772 and 7776 is padding.
        self.ext_offset -= 7776 - round_down_word(compressed_len)

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

        # I think these are all scenes for the clock, but not 100% sure.
        # The giant lookup table references all these
        self.move_to_compressed_memory(0xBE60, 11620, None)

        # Starting here are BALL references
        self.move_to_compressed_memory(0xEBC4, 528, 0x4154)
        self.rwdata_lookup(0xEBC4, 528)

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
            self.ext_offset -= mario_song_len

            self.internal.asm(0x6FC8, "b 0x1c")
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
            self.rwdata_lookup(0x1_2D44, mario_song_len)

        # Each tile is 16x16 pixels, stored as 256 bytes in row-major form.
        # These index into one of the palettes starting at 0xbec68.
        printe("Compressing clock graphics")
        compressed_len = self.external.compress(0x9_8B84, 0x1_0000)
        self.internal.bl(0x678E, "memcpy_inflate")

        printe("Moving clock graphics")
        self.move_ext(0x9_8B84, compressed_len, 0x7350)
        self.ext_offset -= 0x1_0000 - round_down_word(compressed_len)

        # Note: the clock uses a different palette; this palette only applies
        # to ingame Super Mario Bros 1 & 2
        printe("Moving NES emulator palette.")
        self.move_to_compressed_memory(0xA_8B84, 192, 0xB720)

        # Note: UNKNOWN* represents a block of data that i haven't decoded
        # yet. If you know what the block of data is, please let me know!
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
        smb2 = smb2_remove_crc_gaps(smb2)
        (build_dir / "smb2.fds").write_bytes(smb2)

        if self.args.no_smb2:
            printe("Erasing SMB2 ROM")
            self.external.replace(
                smb2_addr,
                b"\x00" * smb2_size,
            )
            self.ext_offset -= smb2_size
        else:
            printe("Compressing and moving SMB2 ROM.")
            compressed_len = self.external.compress(smb2_addr, smb2_size)
            self.internal.bl(0x6A12, "memcpy_inflate")
            self.move_to_compressed_memory(smb2_addr, compressed_len, 0x7374)
            self.ext_offset -= smb2_size - round_down_word(
                compressed_len
            )  # Move by the space savings.

            # Round to nearest page so that the length can be used as an imm
            compressed_len = round_up_page(compressed_len)

            # Update the length of the compressed data (doesn't matter if its too large)
            self.internal.asm(0x6A0A, f"mov.w r2, #{compressed_len}")
            self.internal.asm(0x6A1E, f"mov.w r3, #{compressed_len}")

        # Not sure what this data is
        self.move_to_compressed_memory(0xBEC58, 8 * 2, 0x10964)

        printe("Moving Palettes")
        # There are 80 colors, each in BGRA format, where A is always 0
        # These are referenced by the scene table.
        self.move_to_compressed_memory(0xBEC68, 320, None)  # Day palette [0600, 1700]
        self.move_to_compressed_memory(0xBEDA8, 320, None)  # Night palette [1800, 0400)
        self.move_to_compressed_memory(
            0xBEEE8, 320, None
        )  # Underwater palette (between 1200 and 2400 at XX:30)
        self.move_to_compressed_memory(
            0xBF028, 320, None
        )  # Unknown palette. Maybe bowser castle? need to check...
        self.move_to_compressed_memory(0xBF168, 320, None)  # Dawn palette [0500, 0600)

        # These are scene headers, each containing 2x uint32_t's.
        # They are MOSTLY [0x36, 0xF], but there are a few like [0x30, 0xF] and [0x20, 0xF],
        # Referenced by the scene table
        self.move_to_compressed_memory(0xBF2A8, 45 * 8, None)

        # IDK what this is.
        self.move_to_compressed_memory(0xBF410, 144, 0x1658C)

        # SCENE TABLE
        # Goes in chunks of 20 bytes (5 addresses)
        # Each scene is represented by 5 pointers:
        #    1. Pointer to a 2x uint32_t header (I think it's total tile (w, h) )
        #            The H is always 15, which would be 240 pixels tall.
        #            The W is usually 54, which would be 864 pixels (probably the flag pole?)
        #    2. RLE something. Usually 32 bytes.
        #    3. RLE something
        #    4. RLE something
        #    5. Palette
        #
        # The RLE encoded data could be background tilemap, animation routine, etc.
        lookup_table_start = 0xB_F4A0
        lookup_table_end = 0xB_F838
        lookup_table_len = lookup_table_end - lookup_table_start  # 46 * 5 * 4 = 920
        for addr in range(lookup_table_start, lookup_table_end, 4):
            self.external.lookup(addr)

        # Now move the table
        self.move_to_compressed_memory(lookup_table_start, lookup_table_len, 0xDF88)

        # Not sure what this is
        references = [
            0xE8F8,
            0xF4EC,
            0xF4F8,
            0x10098,
            0x105B0,
        ]
        self.move_to_compressed_memory(0xBF838, 280, references)

        self.move_to_compressed_memory(0xBF950, 180, [0xE2E4, 0xF4FC])
        self.move_to_compressed_memory(0xBFA04, 8, 0x1_6590)
        self.move_to_compressed_memory(0xBFA0C, 784, 0x1_0F9C)

        # MOVE EXTERNAL FUNCTIONS
        new_loc = self.move_ext(0xB_FD1C, 14244, None)
        references = [  # internal references to external functions
            0x00D330,
            0x00D310,
            0x00D308,
            0x00D338,
            0x00D348,
            0x00D360,
            0x00D368,
            0x00D388,
            0x00D358,
            0x00D320,
            0x00D350,
            0x00D380,
            0x00D378,
            0x00D318,
            0x00D390,
            0x00D370,
            0x00D340,
            0x00D398,
            0x00D328,
        ]
        for reference in references:
            self.internal.lookup(reference)

        references = [  # external references to external functions
            0xC_1174,
            0xC_313C,
            0xC_049C,
            0xC_1178,
            0xC_220C,
            0xC_3490,
            0xC_3498,
        ]
        for reference in references:
            reference = reference - 0xB_FD1C + new_loc
            try:
                self.internal.lookup(reference)
            except (IndexError, KeyError):
                self.external.lookup(reference)

        # BALL sound samples.
        self.move_to_compressed_memory(0xC34C0, 6168, 0x43EC)
        self.rwdata_lookup(0xC34C0, 6168)
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
            self.ext_offset -= total_image_length
        else:
            self.move_ext(0xC58F8, total_image_length, references)

        # Definitely at least contains part of the TIME graphic on startup screen.
        self.move_to_compressed_memory(0xF4D18, 2880, 0x10960)

        # What is this data?
        # The memcpy to this address is all zero, so i guess its not used?
        self.external.replace(0xF5858, b"\x00" * 34728)  # refence at internal 0x7210
        self.ext_offset -= 34728

        if self.compressed_memory_pos:
            # Compress and copy over compressed_memory
            self.internal.rwdata.append(
                self.compressed_memory[: self.compressed_memory_pos].copy(),
                self.compressed_memory.FLASH_BASE,
            )

        # Compress, insert, and reference the modified rwdata
        self.int_pos += self.internal.rwdata.write_table_and_data(self.int_pos)

        # Shorten the external firmware
        # This rounds the negative self.ext_offset towards zero.
        self.ext_offset = round_up_page(self.ext_offset)

        if self.args.no_save:
            # Disable nvram loading
            for nop in [0x495E, 0x49A6, 0x49B2]:
                self.internal.nop(nop, 2)
            # self.internal.b(0x4988, 0x49be)  # If you still want the first-startup "Press TIME Button" screen
            self.internal.b(0x4988, 0x49C0)  # Skips Press TIME Button screen

            # Disable nvram saving
            # This just skips the body of the nvram_write_bank function
            self.internal.b(0x48BE, 0x4912)

            self.ext_offset -= 8192
        else:
            printi("Update NVRAM read addresses")
            self.internal.asm(
                0x4856,
                "ite ne; "
                f"movne.w r4, #{hex(0xff000 + self.ext_offset)}; "
                f"moveq.w r4, #{hex(0xfe000 + self.ext_offset)}",
            )
            printi("Update NVRAM write addresses")
            self.internal.asm(
                0x48C0,
                "ite ne; "
                f"movne.w r4, #{hex(0xff000 + self.ext_offset)}; "
                f"moveq.w r4, #{hex(0xfe000 + self.ext_offset)}",
            )

        # Finally, shorten the firmware
        printi("Updating end of OTFDEC pointer")
        self.internal.add(0x1_06EC, self.ext_offset)
        self.external.shorten(self.ext_offset)

        internal_remaining_free = len(self.internal) - self.int_pos
        compressed_memory_free = (
            len(self.compressed_memory) - self.compressed_memory_pos
        )

        return internal_remaining_free, compressed_memory_free


def smb2_remove_crc_gaps(smb2):
    """Remove each block's CRC padding so it can be played by FDS
    https://wiki.nesdev.org/w/index.php/FDS_disk_format
    """
    offset = 0x0

    def get_block(size, crc_gap=2):
        nonlocal offset
        block = smb2[offset : offset + size]
        offset += size + crc_gap
        return block

    disk_info_block = get_block(0x38)

    file_amount_block = get_block(0x2)
    assert file_amount_block[0] == 0x02
    n_files = file_amount_block[1]

    blocks = [disk_info_block, file_amount_block]
    for i in range(n_files):
        file_header_block = get_block(0x10)
        assert file_header_block[0] == 3
        blocks.append(file_header_block)

        file_size = int.from_bytes(file_header_block[13 : 13 + 2], "little")
        file_data_block = get_block(file_size + 1)
        blocks.append(file_data_block)

    out = b"".join(blocks)

    # Zero pad to be 65500 bytes long
    padding = b"\x00" * (65500 - len(out))
    out += padding

    return out
