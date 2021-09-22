from math import ceil, floor
from colorama import Fore, Back, Style

from .exception import ParsingError, NotEnoughSpaceError
from .compression import lzma_compress

def printi(msg, *args):
    print(Fore.MAGENTA + msg + Style.RESET_ALL, *args)
def printe(msg, *args):
    print(Fore.YELLOW + msg + Style.RESET_ALL, *args)
def printd(msg, *args):
    print(Fore.BLUE + msg + Style.RESET_ALL, *args)

def _round_down_word(val):
    return (val // 4) * 4

def _round_up_word(val):
    return ceil(val  / 4) * 4

def _round_down_page(val):
    return (val // 4096) * 4096

def _round_up_page(val):
    return ceil(val  / 4096) * 4096

def _seconds_to_frames(seconds):
    return int(round(60 * seconds))

def _check_int_size(args, int_pos):
    size = 0x20000
    if args.extended:
        size += 0x20000

    if int_pos > size:
        raise IndexError(f"Internal firmware pos {int_pos} exceeded internal firmware size {size}.")

def add_patch_args(parser):

    group = parser.add_mutually_exclusive_group()
    group.add_argument("--disable-sleep", action="store_true",
                        help="Disables sleep timer")
    group.add_argument("--sleep-time", type=float, default=None,
                        help="Go to sleep after this many seconds of inactivity.. "
                         "Valid range: [1, 1092]"
                        )

    parser.add_argument("--hard-reset-time", type=float, default=None,
                         help="Hold power button for this many seconds to perform hard reset."
                         )
    parser.add_argument("--mario-song-time", type=float, default=None,
                         help="Hold the A button for this many seconds on the time "
                         "screen to launch the mario drawing song easter egg."
                         )

    parser.add_argument("--slim", action="store_true",
                        help="Remove mario song and sleeping images from extflash. Perform other space-saving measures.")
    parser.add_argument("--clock-only", action="store_true",
                        help="Everything in --slim plus remove SMB2. TODO: remove Ball.")
    parser.add_argument("--no-save", action="store_true",
                        help="Don't use up 2 pages (8192 bytes) of extflash for non-volatile saves.")
    parser.add_argument("--encrypt", action="store_true",
                        help="Enable OTFDEC for the main extflash binary.")
    parser.add_argument("--no-smb2", action="store_true",
                        help="Remove SMB2 rom.")

    parser.add_argument("--compression-ratio", type=float, default=1.3,
                        help="Data targeted for SRAM3 will only be put into "
                        "SRAM3 if it's compression ratio is above this value. "
                        "Otherwise, will fallback to internal flash, then external "
                        "flash."
                        )


def validate_patch_args(parser, args):
    if args.sleep_time and (args.sleep_time < 1 or args.sleep_time > 1092):
        parser.error("--sleep-time must be in range [1, 1092]")
    if args.mario_song_time and (args.mario_song_time < 1 or args.mario_song_time > 1092):
        parser.error("--mario_song-time must be in range [1, 1092]")

    if args.clock_only:
        args.slim = True

    if args.clock_only:
        args.no_smb2 = True


def _print_rwdata_ext_references(rwdata):
    """
    For debugging/development purposes.
    """
    ls = {}
    for i in range(0, len(rwdata), 4):
        val = int.from_bytes(rwdata[i:i+4], 'little')
        if 0x9000_0000 <= val <= 0x9010_0000:
            ls[val] = i
    for k, val in sorted(ls.items()):
        print(f"0x{k:08X}: 0x{val:06X}")



def find_free_space(device):
    # Detect a series of 0x00 to figure out the end of the patch.
    for addr in range(device.internal.rwdata.table_end, device.internal.FLASH_LEN, 0x10):
        if device.internal[addr:addr+256]  == b"\x00" * 256:
            int_pos_start = addr
            break
    else:
        raise ParsingError("Couldn't find end of internal code.")
    return int_pos_start

def apply_patches(args, device):
    offset = 0
    int_pos = find_free_space(device)
    sram3_pos = 0

    def sram3_compressed_len(add_index=0):
        index = sram3_pos + add_index
        if not index:
            return 0

        data = bytes(device.sram3[:index])
        try:
            return sram3_compressed_len.memo[data]
        except KeyError:
            pass
        compressed_data = lzma_compress(data)
        sram3_compressed_len.memo[data] = len(compressed_data)
        return len(compressed_data)
    sram3_compressed_len.memo = {}

    def int_free_space():
        return len(device.internal) - int_pos - sram3_compressed_len()

    def sram3_free_space():
        return len(device.sram3) - sram3_pos

    def rwdata_lookup(lower, size):
        lower += 0x9000_0000
        upper = lower + size

        for i in range(0, len(device.internal.rwdata[1]), 4):
            val = int.from_bytes(device.internal.rwdata[1][i:i+4], 'little')
            if lower <= val < upper:
                new_val = device.lookup[val]
                print(f"    updating rwdata 0x{val:08X} -> 0x{new_val:08X}")
                device.internal.rwdata[1][i:i+4] = new_val.to_bytes(4, "little")

    def rwdata_erase(lower, size):
        """
        Erasing no longer used references makes it compress better.
        """
        lower += 0x9000_0000
        upper = lower + size

        for i in range(0, len(device.internal.rwdata[1]), 4):
            val = int.from_bytes(device.internal.rwdata[1][i:i+4], 'little')
            if lower <= val < upper:
                device.internal.rwdata[1][i:i+4] = b"\x00\x00\x00\x00"

    def move_to_int(ext, size, reference):
        nonlocal int_pos
        device.move_to_int(ext, int_pos, size=size)
        print(f"    move_to_int {hex(ext)} -> {hex(int_pos)}")
        if reference is not None:
            device.internal.lookup(reference)
        new_loc = int_pos
        int_pos += _round_up_word(size)
        return new_loc

    def move_to_sram3(ext, size, reference):
        nonlocal sram3_pos, offset

        current_len = sram3_compressed_len()

        if False:
            # TODO: Call move_to_int if sram3 is full.
            # TODO: Call device.external.move if internal is full
            raise NotImplementedError

        device.sram3[sram3_pos:sram3_pos + size] = device.external[ext:ext+size]
        new_len = sram3_compressed_len(size)
        compression_ratio = size / (new_len - current_len)
        print(f"    {Fore.YELLOW}compression_ratio: {compression_ratio}{Style.RESET_ALL}")

        if compression_ratio < args.compression_ratio:
            # Revert putting this data into sram3 due to poor space_savings
            print(f"        {Fore.RED}not putting in sram due to poor compression.{Style.RESET_ALL}")
            device.sram3.clear_range(sram3_pos, sram3_pos + size)
            return move_ext_extended(ext, size, reference)
        # Even though the data is already moved, this builds the reference lookup
        device.move_to_sram3(ext, sram3_pos, size=size)

        print(f"    move_to_sram3 {hex(ext)} -> {hex(sram3_pos)}")
        if reference is not None:
            device.internal.lookup(reference)
        new_loc = sram3_pos
        sram3_pos += _round_up_word(size)
        offset -= _round_up_word(size)
        return new_loc

    def move_ext_external(ext, size, reference):
        device.external.move(ext, offset, size=size)
        if reference is not None:
            device.internal.lookup(reference)
        new_loc = ext + offset
        return new_loc

    def move_ext_extended(ext, size, reference):
        nonlocal offset
        try:
            new_loc = move_to_int(ext, size, reference)
            offset -= _round_up_word(size)
            return new_loc
        except NotEnoughSpaceError:
            print(f"    {Fore.RED}Not Enough Internal space. Using external flash{Style.RESET_ALL}")
            raise NotImplementedError
            return move_ext_external(ext, size, reference)

    move_ext = move_ext_extended if args.extended else move_ext_external

    printi("Invoke custom bootloader prior to calling stock Reset_Handler.")
    device.internal.replace(0x4, "bootloader")

    printi("Intercept button presses for macros.")
    device.internal.bl(0x6b52, "read_buttons")

    printi("Mute clock audio on first boot.")
    device.internal.asm(0x49e0, "mov.w r1, #0x00000")

    if args.debug:
        # Override fault handlers for easier debugging via gdb.
        printi("Overriding handlers for debugging.")
        device.internal.replace(0x8, "NMI_Handler")
        device.internal.replace(0xC, "HardFault_Handler")

    if args.hard_reset_time:
        hard_reset_time_ms = int(round(args.hard_reset_time * 1000))
        printi(f"Hold power button for {hard_reset_time_ms} milliseconds to perform hard reset.")
        device.internal.asm(0x9cee, f"movw r1, #{hard_reset_time_ms}")

    if args.sleep_time:
        printi(f"Setting sleep time to {args.sleep_time} seconds.")
        sleep_time_frames = _seconds_to_frames(args.sleep_time)
        device.internal.asm(0x6c3c, f"movw r2, #{sleep_time_frames}")

    if args.disable_sleep:
        printi(f"Disable sleep timer")
        device.internal.replace(0x6C40, 0x91, size=1)

    if args.mario_song_time:
        printi(f"Setting Mario Song time to {args.mario_song_time} seconds.")
        mario_song_frames = _seconds_to_frames(args.mario_song_time)
        device.internal.asm(0x6fc4, f"cmp.w r0, #{mario_song_frames}")

    if not args.encrypt:
        # Disable OTFDEC
        device.internal.nop(0x10688, 2)
        device.internal.nop(0x1068e, 1)


    printd("Compressing and moving stuff stuff to internal firmware.")
    compressed_len = device.external.compress(0x0, 7772)
    device.internal.bl(0x665c, "memcpy_inflate")
    move_ext(0x0, compressed_len, 0x7204)
    offset -= (7772 - _round_down_word(compressed_len))

    # SMB1 ROM
    printd(f"Compressing and moving SMB1 ROM to sram3.")
    move_to_sram3(0x1e60, 40960, [0x7368, 0x10954, 0x7218])

    # I think these are all scenes for the clock, but not 100% sure.
    # The giant lookup table references all these
    move_to_sram3(0xbe60, 11620, None)

    # Starting here are BALL references
    move_to_sram3(0xebc4, 528, 0x4154)
    rwdata_lookup(0xebc4, 528)

    move_to_sram3(0xedd4, 100, 0x4570)

    references = {
        0xee38: 0x4514,
        0xee78: 0x4518,
        0xeeb8: 0x4520,
        0xeef8: 0x4524,
    }
    for external, internal in references.items():
        move_ext(external, 64, internal)

    references = [
        0x2ac,
        0x2b0,
        0x2b4,
        0x2b8,
        0x2bc,
        0x2c0,
        0x2c4,
        0x2c8,
        0x2cc,
        0x2d0,
    ]
    move_to_sram3(0xef38, 128*10, references)

    move_ext(0xf438, 96, 0x456c)
    move_ext(0xf498, 180, 0x43f8)

    # This is the first thing passed into the drawing engine.
    move_to_sram3(0xf54c, 1100, 0x43fc)
    move_to_sram3(0xf998, 180, 0x4400)
    move_to_sram3(0xfa4c, 1136, 0x4404)
    move_to_sram3(0xfebc, 864, 0x450c)
    move_to_sram3(0x1_021c, 384, 0x4510)
    move_to_sram3(0x1_039c, 384, 0x451c)
    move_to_sram3(0x1_051c, 384, 0x4410)
    move_to_sram3(0x1_069c, 384, 0x44f8)
    move_to_sram3(0x1_081c, 384, 0x4500)
    move_to_sram3(0x1_099c, 384, 0x4414)
    move_to_sram3(0x1_0b1c, 384, 0x44fc)
    move_to_sram3(0x1_0c9c, 384, 0x4504)
    move_to_sram3(0x1_0e1c, 384, 0x440c)
    move_to_sram3(0x1_0f9c, 384, 0x4408)
    move_to_sram3(0x1_111c, 192, 0x44f4)
    move_to_sram3(0x1_11dc, 192, 0x4508)
    move_to_sram3(0x1_129c, 304, 0x458c)
    move_to_sram3(0x1_13cc, 768, 0x4584)  # BALL logo tile idx tight
    move_to_sram3(0x1_16cc, 1144, 0x4588)
    move_to_sram3(0x1_1b44, 768, 0x4534)
    move_to_sram3(0x1_1e44, 32, 0x455c)
    move_to_sram3(0x1_1e64, 32, 0x4558)
    move_to_sram3(0x1_1e84, 32, 0x4554)
    move_to_sram3(0x1_1ea4, 32, 0x4560)
    move_to_sram3(0x1_1ec4, 32, 0x4564)
    move_to_sram3(0x1_1ee4, 64, 0x453c)
    move_to_sram3(0x1_1f24, 64, 0x4530)
    move_to_sram3(0x1_1f64, 64, 0x4540)
    move_to_sram3(0x1_1fa4, 64, 0x4544)
    move_to_sram3(0x1_1fe4, 64, 0x4548)
    move_to_sram3(0x1_2024, 64, 0x454c)
    move_to_sram3(0x1_2064, 64, 0x452c)
    move_to_sram3(0x1_20a4, 64, 0x4550)

    move_to_sram3(0x1_20e4, 21 * 96, 0x4574)
    move_to_sram3(0x1_28c4, 192, 0x4578)
    move_to_sram3(0x1_2984, 640, 0x457c)

    # This is a 320 byte palette used for BALL, but the last 160 bytes are empty
    move_to_sram3(0x1_2c04, 320, 0x4538)


    if args.slim:
        mario_song_len = 0x85e40  # 548,416 bytes
        # This isn't really necessary, but we keep it here because its more explicit.
        printe("Erasing Mario Song")
        device.external.replace(0x1_2D44, b"\x00" * mario_song_len)
        rwdata_erase(0x1_2D44, mario_song_len)
        offset -= mario_song_len

    # Each tile is 16x16 pixels, stored as 256 bytes in row-major form.
    # These index into one of the palettes starting at 0xbec68.
    printe("Compressing clock graphics")
    compressed_len = device.external.compress(0x9_8b84, 0x1_0000)
    device.internal.bl(0x678e, "memcpy_inflate")

    printe("Moving clock graphics to internal firmware")
    move_ext(0x9_8b84, compressed_len, 0x7350)
    offset -= (0x1_0000 - _round_down_word(compressed_len))

    # Note: the clock uses a different palette; this palette only applies
    # to ingame Super Mario Bros 1 & 2
    printe("Moving NES emulator palette.")
    move_to_sram3(0xa_8b84, 192, 0xb720)

    # Note: UNKNOWN* represents a block of data that i haven't decoded
    # yet. If you know what the block of data is, please let me know!
    move_to_sram3(0xa_8c44, 8352, 0xbc44)

    printe("Moving iconset.")
    # MODIFY THESE IF WE WANT CUSTOM GAME ICONS
    move_to_sram3(0xa_ace4, 16128, [0xcea8, 0xd2f8])

    printe("Moving menu stuff (icons? meta?)")
    references = [
        0x0_d010,
        0x0_d004,
        0x0_d2d8,
        0x0_d2dc,
        0x0_d2f4,
        0x0_d2f0,
    ]
    move_ext(0xa_ebe4, 116, references)

    if args.no_smb2:
        printe("Erasing SMB2 ROM")
        device.external.replace(0xa_ec58, b"\x00" * 65536,)
        offset -= 65536
    else:
        printe("Compressing and moving SMB2 ROM.")
        compressed_len = device.external.compress(0xa_ec58, 0x1_0000)
        device.internal.bl(0x6a12, "memcpy_inflate")
        move_ext(0xa_ec58, compressed_len, 0x7374)
        offset -= (65536 - _round_down_word(compressed_len))  # Move by the space savings.

        # Round to nearest page so that the length can be used as an imm
        compressed_len = _round_up_page(compressed_len)

        # Update the length of the compressed data (doesn't matter if its too large)
        device.internal.asm(0x6a0a, f"mov.w r2, #{compressed_len}")
        device.internal.asm(0x6a1e, f"mov.w r3, #{compressed_len}")

    # Not sure what this data is
    move_ext(0xbec58, 8*2, 0x10964)

    printe("Moving Palettes")
    # There are 80 colors, each in BGRA format, where A is always 0
    # These are referenced by the scene table.
    move_to_sram3(0xbec68, 320, None)  # Day palette [0600, 1700]
    move_to_sram3(0xbeda8, 320, None)  # Night palette [1800, 0400)
    move_to_sram3(0xbeee8, 320, None)  # Underwater palette (between 1200 and 2400 at XX:30)
    move_to_sram3(0xbf028, 320, None)  # Unknown palette. Maybe bowser castle? need to check...
    move_to_sram3(0xbf168, 320, None)  # Dawn palette [0500, 0600)

    # These are scene headers, each containing 2x uint32_t's.
    # They are MOSTLY [0x36, 0xF], but there are a few like [0x30, 0xF] and [0x20, 0xF],
    # Referenced by the scene table
    move_to_sram3(0xbf2a8, 45 * 8, None)

    # IDK what this is.
    move_to_sram3(0xbf410, 144, 0x1658c)

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
    lookup_table_start = 0xb_f4a0
    lookup_table_end   = 0xb_f838
    lookup_table_len   = lookup_table_end - lookup_table_start  # 46 * 5 * 4 = 920
    for addr in range(lookup_table_start, lookup_table_end, 4):
        device.external.lookup(addr)

    # Now move the table
    move_to_sram3(lookup_table_start, lookup_table_len, 0xdf88)

    # Not sure what this is
    references = [
        0xe8f8,
        0xf4ec,
        0xf4f8,
        0x10098,
        0x105b0,
    ]
    move_to_sram3(0xbf838, 280, references)

    move_to_sram3(0xbf950, 180, [0xe2e4, 0xf4fc])
    move_ext(0xbfa04, 8, 0x1_6590)
    move_ext(0xbfa0c, 784, 0x1_0f9c)

    # MOVE EXTERNAL FUNCTIONS
    new_loc = move_ext(0xb_fd1c, 14244, None)
    references = [  # internal references to external functions
        0x00d330,
        0x00d310,
        0x00d308,
        0x00d338,
        0x00d348,
        0x00d360,
        0x00d368,
        0x00d388,
        0x00d358,
        0x00d320,
        0x00d350,
        0x00d380,
        0x00d378,
        0x00d318,
        0x00d390,
        0x00d370,
        0x00d340,
        0x00d398,
        0x00d328,
    ]
    for reference in references:
        device.internal.lookup(reference)

    references = [  # external references to external functions
        0xc_1174,
        0xc_313c,
        0xc_049c,
        0xc_1178,
        0xc_220c,
        0xc_3490,
        0xc_3498,
    ]
    for reference in references:
        reference = reference - 0xb_fd1c + new_loc
        # BUG: THIS IS PROBLEMATIC
        if args.extended:
            device.internal.lookup(reference)
        else:
            device.external.lookup(reference)

    # BALL sound samples.
    move_ext(0xc34c0, 6168, 0x43ec)
    rwdata_lookup(0xc34c0, 6168)
    move_ext(0xc4cd8, 2984, 0x459c)
    move_ext(0xc5880, 120, 0x4594)

    if args.slim:
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
        device.external.replace(0xc58f8, b"\x00" * total_image_length)
        device.internal.replace(0x1097c, b"\x00"*4*5)  # Erase image references
        offset -= total_image_length

    # Definitely at least contains part of the TIME graphic on startup screen.
    move_to_sram3(0xf4d18, 2880, 0x10960)

    # What is this data?
    # The memcpy to this address is all zero, so i guess its not used?
    device.external.replace(0xf5858, b"\x00" * 34728)  # refence at internal 0x7210
    offset -= 34728

    if sram3_pos:
        # Compress and copy over SRAM3
        device.internal.rwdata.append(device.sram3[:sram3_pos].copy(), device.sram3.FLASH_BASE)

    # Compress, insert, and reference the modified rwdata
    int_pos += device.internal.rwdata.write_table_and_data(int_pos)

    # Shorten the external firmware
    # This rounds the negative offset towards zero.
    offset = _round_up_page(offset)

    if args.no_save:
        # Disable nvram loading
        for nop in [0x495e, 0x49a6, 0x49b2]:
            device.internal.nop(nop, 2)
        #device.internal.b(0x4988, 0x49be)  # If you still want the first-startup "Press TIME Button" screen
        device.internal.b(0x4988, 0x49c0)  # Skips Press TIME Button screen

        # Disable nvram saving
        # This just skips the body of the nvram_write_bank function
        device.internal.b(0x48be, 0x4912)

        offset -= 8192
    else:
        printi("Update NVRAM read addresses")
        device.internal.asm(0x4856,
                 "ite ne; "
                f"movne.w r4, #{hex(0xff000 + offset)}; "
                f"moveq.w r4, #{hex(0xfe000 + offset)}",
        )
        printi("Update NVRAM write addresses")
        device.internal.asm(0x48c0,
                 "ite ne; "
                f"movne.w r4, #{hex(0xff000 + offset)}; "
                f"moveq.w r4, #{hex(0xfe000 + offset)}",
        )

    # Finally, shorten the firmware
    printi("Updating end of OTFDEC pointer")
    device.internal.add(0x1_06ec, offset)
    device.external.shorten(offset)

    internal_remaining_free = len(device.internal) - int_pos
    sram3_free = len(device.sram3) - sram3_pos

    return internal_remaining_free, sram3_free
