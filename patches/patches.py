from math import ceil, floor
from pathlib import Path

from colorama import Back, Fore, Style

from .compression import lzma_compress
from .exception import NotEnoughSpaceError, ParsingError


def printi(msg, *args):
    print(Fore.MAGENTA + msg + Style.RESET_ALL, *args)


def printe(msg, *args):
    print(Fore.YELLOW + msg + Style.RESET_ALL, *args)


def printd(msg, *args):
    print(Fore.BLUE + msg + Style.RESET_ALL, *args)


def _round_down_word(val):
    return (val // 4) * 4


def _round_up_word(val):
    return ceil(val / 4) * 4


def _round_down_page(val):
    return (val // 4096) * 4096


def _round_up_page(val):
    return ceil(val / 4096) * 4096


def _seconds_to_frames(seconds):
    return int(round(60 * seconds))


def add_patch_args(parser):
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

    group = parser.add_argument_group("ROM Hacks")
    group.add_argument(
        "--smb1",
        type=Path,
        default="build/smb1.nes",
        help="Override SMB1 ROM with your own file.",
    )

    group = parser.add_argument_group("Low level flash savings flags")
    group.add_argument(
        "--no-save",
        action="store_true",
        help="Don't use up 2 pages (8192 bytes) of extflash for non-volatile saves. High scores and brightness/volume configurations will NOT survive homebrew launches.",
    )
    group.add_argument("--no-smb2", action="store_true", help="Remove SMB2 rom.")
    group.add_argument(
        "--no-mario-song", action="store_true", help="Remove the mario song easter egg."
    )
    group.add_argument(
        "--no-sleep-images", action="store_true", help="Remove the 5 sleeping images."
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


def validate_patch_args(parser, args):
    if args.sleep_time and (args.sleep_time < 1 or args.sleep_time > 1092):
        parser.error("--sleep-time must be in range [1, 1092]")
    if args.mario_song_time and (
        args.mario_song_time < 1 or args.mario_song_time > 1092
    ):
        parser.error("--mario_song-time must be in range [1, 1092]")

    if args.internal_only:
        args.slim = True
        args.extended = True
        args.no_save = True

    if args.clock_only:
        args.slim = True
        args.no_smb2 = True

    if args.slim:
        args.no_mario_song = True
        args.no_sleep_images = True


def _print_rwdata_ext_references(rwdata):
    """
    For debugging/development purposes.
    """
    ls = {}
    for i in range(0, len(rwdata), 4):
        val = int.from_bytes(rwdata[i : i + 4], "little")
        if 0x9000_0000 <= val <= 0x9010_0000:
            ls[val] = i
    for k, val in sorted(ls.items()):
        print(f"0x{k:08X}: 0x{val:06X}")


def find_free_space(device):
    # Detect a series of 0x00 to figure out the end of the patch.
    for addr in range(
        device.internal.rwdata.table_end, device.internal.FLASH_LEN, 0x10
    ):
        if device.internal[addr : addr + 256] == b"\x00" * 256:
            int_pos_start = addr
            break
    else:
        raise ParsingError("Couldn't find end of internal code.")
    return int_pos_start


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


def apply_patches(args, device, build):
    offset = 0
    int_pos = find_free_space(device)
    sram3_pos = 0

    def sram3_compressed_len(add_index=0):
        index = sram3_pos + add_index
        if not index:
            return 0

        data = bytes(device.sram3[:index])
        if data in sram3_compressed_len.memo:
            return sram3_compressed_len.memo[data]

        compressed_data = lzma_compress(data)
        sram3_compressed_len.memo[data] = len(compressed_data)
        return len(compressed_data)

    sram3_compressed_len.memo = {}

    def int_free_space(add_index=0):
        return (
            len(device.internal)
            - int_pos
            - sram3_compressed_len(add_index=add_index)
            - device.internal.rwdata.compressed_len
        )

    def sram3_free_space():
        return len(device.sram3) - sram3_pos

    def rwdata_lookup(lower, size):
        lower += 0x9000_0000
        upper = lower + size

        for i in range(0, len(device.internal.rwdata[1]), 4):
            val = int.from_bytes(device.internal.rwdata[1][i : i + 4], "little")
            if lower <= val < upper:
                new_val = device.lookup[val]
                print(f"    updating rwdata 0x{val:08X} -> 0x{new_val:08X}")
                device.internal.rwdata[1][i : i + 4] = new_val.to_bytes(4, "little")

    def rwdata_erase(lower, size):
        """
        Erasing no longer used references makes it compress better.
        """
        lower += 0x9000_0000
        upper = lower + size

        for i in range(0, len(device.internal.rwdata[1]), 4):
            val = int.from_bytes(device.internal.rwdata[1][i : i + 4], "little")
            if lower <= val < upper:
                device.internal.rwdata[1][i : i + 4] = b"\x00\x00\x00\x00"

    def move_to_int(ext, size, reference):
        nonlocal int_pos

        if int_free_space() < size:
            raise NotEnoughSpaceError

        device.move_to_int(ext, int_pos, size=size)
        print(f"    move_to_int {hex(ext)} -> {hex(int_pos)}")
        if reference is not None:
            device.internal.lookup(reference)
        new_loc = int_pos
        int_pos += _round_up_word(size)
        return new_loc

    def move_to_sram3(ext, size, reference):
        """Attempt to relocate in priority order:
        1. SRAM3
        2. Internal
        3. External

        This is the primary moving method for any compressible data.
        """

        nonlocal sram3_pos, offset

        current_len = sram3_compressed_len()

        try:
            device.sram3[sram3_pos : sram3_pos + size] = device.external[
                ext : ext + size
            ]
        except NotEnoughSpaceError:
            print(
                f"        {Fore.RED}sram3 full. Attempting to put in internal{Style.RESET_ALL}"
            )
            return move_ext(ext, size, reference)

        new_len = sram3_compressed_len(size)
        diff = new_len - current_len
        compression_ratio = size / diff

        print(
            f"    {Fore.YELLOW}compression_ratio: {compression_ratio}{Style.RESET_ALL}"
        )

        if diff > int_free_space():
            print(
                f"        {Fore.RED}not putting in sram due not enough free internal storage for compressed data.{Style.RESET_ALL}"
            )
            device.sram3.clear_range(sram3_pos, sram3_pos + size)
            return move_ext_external(ext, size, reference)
        elif compression_ratio < args.compression_ratio:
            # Revert putting this data into sram3 due to poor space_savings
            print(
                f"        {Fore.RED}not putting in sram due to poor compression.{Style.RESET_ALL}"
            )
            device.sram3.clear_range(sram3_pos, sram3_pos + size)
            return move_ext(ext, size, reference)
        # Even though the data is already moved, this builds the reference lookup
        device.move_to_sram3(ext, sram3_pos, size=size)

        print(f"    move_to_sram3 {hex(ext)} -> {hex(sram3_pos)}")
        if reference is not None:
            device.internal.lookup(reference)
        new_loc = sram3_pos
        sram3_pos += _round_up_word(size)
        offset -= _round_down_word(size)

        return new_loc

    def move_ext_external(ext, size, reference):
        device.external.move(ext, offset, size=size)
        if reference is not None:
            device.internal.lookup(reference)
        new_loc = ext + offset
        return new_loc

    def move_ext(ext, size, reference):
        """Attempt to relocate in priority order:
        1. Internal
        2. External

        This is the primary moving function for data that is already compressed
        or is incompressible.
        """
        nonlocal offset
        try:
            new_loc = move_to_int(ext, size, reference)
            offset -= _round_down_word(size)
            return new_loc
        except NotEnoughSpaceError:
            print(
                f"        {Fore.RED}Not Enough Internal space. Using external flash{Style.RESET_ALL}"
            )
            return move_ext_external(ext, size, reference)

    printi("Invoke custom bootloader prior to calling stock Reset_Handler.")
    device.internal.replace(0x4, "bootloader")

    printi("Intercept button presses for macros.")
    device.internal.bl(0x6B52, "read_buttons")

    printi("Mute clock audio on first boot.")
    device.internal.asm(0x49E0, "mov.w r1, #0x00000")

    if args.debug:
        # Override fault handlers for easier debugging via gdb.
        printi("Overriding handlers for debugging.")
        device.internal.replace(0x8, "NMI_Handler")
        device.internal.replace(0xC, "HardFault_Handler")

    if args.hard_reset_time:
        hard_reset_time_ms = int(round(args.hard_reset_time * 1000))
        printi(
            f"Hold power button for {hard_reset_time_ms} milliseconds to perform hard reset."
        )
        device.internal.asm(0x9CEE, f"movw r1, #{hard_reset_time_ms}")

    if args.sleep_time:
        printi(f"Setting sleep time to {args.sleep_time} seconds.")
        sleep_time_frames = _seconds_to_frames(args.sleep_time)
        device.internal.asm(0x6C3C, f"movw r2, #{sleep_time_frames}")

    if args.disable_sleep:
        printi(f"Disable sleep timer")
        device.internal.replace(0x6C40, 0x91, size=1)

    if args.mario_song_time:
        printi(f"Setting Mario Song time to {args.mario_song_time} seconds.")
        mario_song_frames = _seconds_to_frames(args.mario_song_time)
        device.internal.asm(0x6FC4, f"cmp.w r0, #{mario_song_frames}")

    if not args.encrypt:
        # Disable OTFDEC
        device.internal.nop(0x10688, 2)
        device.internal.nop(0x1068E, 1)

    printd("Compressing and moving stuff stuff to internal firmware.")
    compressed_len = device.external.compress(
        0x0, 7772
    )  # Dst expects only 7772 bytes, not 7776
    device.internal.bl(0x665C, "memcpy_inflate")
    move_ext(0x0, compressed_len, 0x7204)
    # Note: the 4 bytes between 7772 and 7776 is padding.
    offset -= 7776 - _round_down_word(compressed_len)

    # SMB1 ROM (plus loading custom ROM)
    printd(f"Compressing and moving SMB1 ROM to sram3.")
    smb1_addr, smb1_size = 0x1E60, 40960
    # Adding the header for patching convenience.
    (build / "smb1.nes").write_bytes(
        b"NES\x1a\x02\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00"
        + device.external[smb1_addr : smb1_addr + smb1_size]
    )
    smb1 = args.smb1.read_bytes()
    if len(smb1) == 40976:
        # Remove the NES header
        smb1 = smb1[16:]
    if len(smb1) != smb1_size:
        raise ValueError(f"Unknown length {len(smb1)} of file {args.smb1}")
    device.external[smb1_addr : smb1_addr + smb1_size] = smb1
    move_to_sram3(smb1_addr, smb1_size, [0x7368, 0x10954, 0x7218])

    # I think these are all scenes for the clock, but not 100% sure.
    # The giant lookup table references all these
    move_to_sram3(0xBE60, 11620, None)

    # Starting here are BALL references
    move_to_sram3(0xEBC4, 528, 0x4154)
    rwdata_lookup(0xEBC4, 528)

    move_to_sram3(0xEDD4, 100, 0x4570)

    references = {
        0xEE38: 0x4514,
        0xEE78: 0x4518,
        0xEEB8: 0x4520,
        0xEEF8: 0x4524,
    }
    for external, internal in references.items():
        move_to_sram3(external, 64, internal)

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
    move_to_sram3(0xEF38, 128 * 10, references)

    move_to_sram3(0xF438, 96, 0x456C)
    move_to_sram3(0xF498, 180, 0x43F8)

    # This is the first thing passed into the drawing engine.
    move_to_sram3(0xF54C, 1100, 0x43FC)
    move_to_sram3(0xF998, 180, 0x4400)
    move_to_sram3(0xFA4C, 1136, 0x4404)
    move_to_sram3(0xFEBC, 864, 0x450C)
    move_to_sram3(0x1_021C, 384, 0x4510)
    move_to_sram3(0x1_039C, 384, 0x451C)
    move_to_sram3(0x1_051C, 384, 0x4410)
    move_to_sram3(0x1_069C, 384, 0x44F8)
    move_to_sram3(0x1_081C, 384, 0x4500)
    move_to_sram3(0x1_099C, 384, 0x4414)
    move_to_sram3(0x1_0B1C, 384, 0x44FC)
    move_to_sram3(0x1_0C9C, 384, 0x4504)
    move_to_sram3(0x1_0E1C, 384, 0x440C)
    move_to_sram3(0x1_0F9C, 384, 0x4408)
    move_to_sram3(0x1_111C, 192, 0x44F4)
    move_to_sram3(0x1_11DC, 192, 0x4508)
    move_to_sram3(0x1_129C, 304, 0x458C)
    move_to_sram3(0x1_13CC, 768, 0x4584)  # BALL logo tile idx tight
    move_to_sram3(0x1_16CC, 1144, 0x4588)
    move_to_sram3(0x1_1B44, 768, 0x4534)
    move_to_sram3(0x1_1E44, 32, 0x455C)
    move_to_sram3(0x1_1E64, 32, 0x4558)
    move_to_sram3(0x1_1E84, 32, 0x4554)
    move_to_sram3(0x1_1EA4, 32, 0x4560)
    move_to_sram3(0x1_1EC4, 32, 0x4564)
    move_to_sram3(0x1_1EE4, 64, 0x453C)
    move_to_sram3(0x1_1F24, 64, 0x4530)
    move_to_sram3(0x1_1F64, 64, 0x4540)
    move_to_sram3(0x1_1FA4, 64, 0x4544)
    move_to_sram3(0x1_1FE4, 64, 0x4548)
    move_to_sram3(0x1_2024, 64, 0x454C)
    move_to_sram3(0x1_2064, 64, 0x452C)
    move_to_sram3(0x1_20A4, 64, 0x4550)

    move_to_sram3(0x1_20E4, 21 * 96, 0x4574)
    move_to_sram3(0x1_28C4, 192, 0x4578)
    move_to_sram3(0x1_2984, 640, 0x457C)

    # This is a 320 byte palette used for BALL, but the last 160 bytes are empty
    move_to_sram3(0x1_2C04, 320, 0x4538)

    if args.no_mario_song:
        mario_song_len = 0x85E40  # 548,416 bytes
        # This isn't really necessary, but we keep it here because its more explicit.
        printe("Erasing Mario Song")
        device.external.replace(0x1_2D44, b"\x00" * mario_song_len)
        rwdata_erase(0x1_2D44, mario_song_len)
        offset -= mario_song_len

    # Each tile is 16x16 pixels, stored as 256 bytes in row-major form.
    # These index into one of the palettes starting at 0xbec68.
    printe("Compressing clock graphics")
    compressed_len = device.external.compress(0x9_8B84, 0x1_0000)
    device.internal.bl(0x678E, "memcpy_inflate")

    printe("Moving clock graphics")
    move_ext(0x9_8B84, compressed_len, 0x7350)
    offset -= 0x1_0000 - _round_down_word(compressed_len)

    # Note: the clock uses a different palette; this palette only applies
    # to ingame Super Mario Bros 1 & 2
    printe("Moving NES emulator palette.")
    move_to_sram3(0xA_8B84, 192, 0xB720)

    # Note: UNKNOWN* represents a block of data that i haven't decoded
    # yet. If you know what the block of data is, please let me know!
    move_to_sram3(0xA_8C44, 8352, 0xBC44)

    printe("Moving iconset.")
    # MODIFY THESE IF WE WANT CUSTOM GAME ICONS
    move_to_sram3(0xA_ACE4, 16128, [0xCEA8, 0xD2F8])

    printe("Moving menu stuff (icons? meta?)")
    references = [
        0x0_D010,
        0x0_D004,
        0x0_D2D8,
        0x0_D2DC,
        0x0_D2F4,
        0x0_D2F0,
    ]
    move_to_sram3(0xA_EBE4, 116, references)

    # Dump a playable version of SMB2
    smb2_addr, smb2_size = 0xA_EC58, 0x1_0000
    smb2_end = smb2_addr + smb2_size
    smb2 = device.external[smb2_addr:smb2_end].copy()
    smb2 = smb2_remove_crc_gaps(smb2)
    (build / "smb2.fds").write_bytes(smb2)

    if args.no_smb2:
        printe("Erasing SMB2 ROM")
        device.external.replace(
            smb2_addr,
            b"\x00" * smb2_size,
        )
        offset -= smb2_size
    else:
        printe("Compressing and moving SMB2 ROM.")
        compressed_len = device.external.compress(smb2_addr, smb2_size)
        device.internal.bl(0x6A12, "memcpy_inflate")
        move_to_sram3(smb2_addr, compressed_len, 0x7374)
        offset -= smb2_size - _round_down_word(
            compressed_len
        )  # Move by the space savings.

        # Round to nearest page so that the length can be used as an imm
        compressed_len = _round_up_page(compressed_len)

        # Update the length of the compressed data (doesn't matter if its too large)
        device.internal.asm(0x6A0A, f"mov.w r2, #{compressed_len}")
        device.internal.asm(0x6A1E, f"mov.w r3, #{compressed_len}")

    # Not sure what this data is
    move_to_sram3(0xBEC58, 8 * 2, 0x10964)

    printe("Moving Palettes")
    # There are 80 colors, each in BGRA format, where A is always 0
    # These are referenced by the scene table.
    move_to_sram3(0xBEC68, 320, None)  # Day palette [0600, 1700]
    move_to_sram3(0xBEDA8, 320, None)  # Night palette [1800, 0400)
    move_to_sram3(
        0xBEEE8, 320, None
    )  # Underwater palette (between 1200 and 2400 at XX:30)
    move_to_sram3(
        0xBF028, 320, None
    )  # Unknown palette. Maybe bowser castle? need to check...
    move_to_sram3(0xBF168, 320, None)  # Dawn palette [0500, 0600)

    # These are scene headers, each containing 2x uint32_t's.
    # They are MOSTLY [0x36, 0xF], but there are a few like [0x30, 0xF] and [0x20, 0xF],
    # Referenced by the scene table
    move_to_sram3(0xBF2A8, 45 * 8, None)

    # IDK what this is.
    move_to_sram3(0xBF410, 144, 0x1658C)

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
        device.external.lookup(addr)

    # Now move the table
    move_to_sram3(lookup_table_start, lookup_table_len, 0xDF88)

    # Not sure what this is
    references = [
        0xE8F8,
        0xF4EC,
        0xF4F8,
        0x10098,
        0x105B0,
    ]
    move_to_sram3(0xBF838, 280, references)

    move_to_sram3(0xBF950, 180, [0xE2E4, 0xF4FC])
    move_to_sram3(0xBFA04, 8, 0x1_6590)
    move_to_sram3(0xBFA0C, 784, 0x1_0F9C)

    # MOVE EXTERNAL FUNCTIONS
    new_loc = move_ext(0xB_FD1C, 14244, None)
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
        device.internal.lookup(reference)

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
            device.internal.lookup(reference)
        except (IndexError, KeyError):
            device.external.lookup(reference)

    # BALL sound samples.
    move_to_sram3(0xC34C0, 6168, 0x43EC)
    rwdata_lookup(0xC34C0, 6168)
    move_to_sram3(0xC4CD8, 2984, 0x459C)
    move_to_sram3(0xC5880, 120, 0x4594)

    if args.no_sleep_images:
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
        total_image_length = 193_568
        device.external.replace(0xC58F8, b"\x00" * total_image_length)
        device.internal.replace(0x1097C, b"\x00" * 4 * 5)  # Erase image references
        offset -= total_image_length

    # Definitely at least contains part of the TIME graphic on startup screen.
    move_to_sram3(0xF4D18, 2880, 0x10960)

    # What is this data?
    # The memcpy to this address is all zero, so i guess its not used?
    device.external.replace(0xF5858, b"\x00" * 34728)  # refence at internal 0x7210
    offset -= 34728

    if sram3_pos:
        # Compress and copy over SRAM3
        device.internal.rwdata.append(
            device.sram3[:sram3_pos].copy(), device.sram3.FLASH_BASE
        )

    # Compress, insert, and reference the modified rwdata
    int_pos += device.internal.rwdata.write_table_and_data(int_pos)

    # Shorten the external firmware
    # This rounds the negative offset towards zero.
    offset = _round_up_page(offset)

    if args.no_save:
        # Disable nvram loading
        for nop in [0x495E, 0x49A6, 0x49B2]:
            device.internal.nop(nop, 2)
        # device.internal.b(0x4988, 0x49be)  # If you still want the first-startup "Press TIME Button" screen
        device.internal.b(0x4988, 0x49C0)  # Skips Press TIME Button screen

        # Disable nvram saving
        # This just skips the body of the nvram_write_bank function
        device.internal.b(0x48BE, 0x4912)

        offset -= 8192
    else:
        printi("Update NVRAM read addresses")
        device.internal.asm(
            0x4856,
            "ite ne; "
            f"movne.w r4, #{hex(0xff000 + offset)}; "
            f"moveq.w r4, #{hex(0xfe000 + offset)}",
        )
        printi("Update NVRAM write addresses")
        device.internal.asm(
            0x48C0,
            "ite ne; "
            f"movne.w r4, #{hex(0xff000 + offset)}; "
            f"moveq.w r4, #{hex(0xfe000 + offset)}",
        )

    # Finally, shorten the firmware
    printi("Updating end of OTFDEC pointer")
    device.internal.add(0x1_06EC, offset)
    device.external.shorten(offset)

    internal_remaining_free = len(device.internal) - int_pos
    sram3_free = len(device.sram3) - sram3_pos

    return internal_remaining_free, sram3_free
