from .patch import Patches


# 0x9009_8b84 onwards (post mario song)
#EXTFLASH_REFERENCES = {
#    0x
#}


def _seconds_to_frames(seconds):
    return int(round(60 * seconds))

def add_patch_args(parser):
    parser.add_argument("--disable-sleep", action="store_true",
                        help="Disables sleep timer")
    parser.add_argument("--sleep-time", type=float, default=None,
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
    parser.add_argument("--slim", action="store_true", default=False,
                        help="Remove mario song from extflash")


def patch_args_validation(parser, args):
    if args.disable_sleep and args.sleep_time:
        parser.error("Conflicting options: cannot specify both --disable-sleep and --sleep-time")
    if args.sleep_time and (args.sleep_time < 1 or args.sleep_time > 1092):
        parser.error("--sleep-time must be in range [1, 1092]")
    if args.mario_song_time and (args.mario_song_time < 1 or args.mario_song_time > 1092):
        parser.error("--mario_song-time must be in range [1, 1092]")


def parse_patches(args):
    patches = Patches()

    patches.append("replace", 0x4, "bootloader",
                   message="Invoke custom bootloader prior to calling stock Reset_Handler")
    patches.append("bl", 0x6b52, "read_buttons",
                   message="Intercept button presses for macros")

    if args.hard_reset_time:
        hard_reset_time_ms = int(round(args.hard_reset_time * 1000))
        patches.append("ks_thumb", 0x9cee, f"movw r1, #{hard_reset_time_ms}", size=4,
                       message=f"Hold power button for {hard_reset_time_ms} "
                                "milliseconds to perform hard reset.")

    if args.sleep_time:
        sleep_time_frames = _seconds_to_frames(args.sleep_time)
        patches.append("ks_thumb", 0x6c3c, f"movw r2, #{sleep_time_frames}", size=4,
                       message=f"Setting sleep time to {args.sleep_time} seconds.")

    if args.disable_sleep:
        patches.append("replace", 0x6C40, 0x91, size=1,
                       message=f"Disable sleep timer")

    if args.mario_song_time:
        mario_song_frames = _seconds_to_frames(args.mario_song_time)
        patches.append("ks_thumb", 0x6fc4, f"cmp.w r0, #{mario_song_frames}", size=4,
                       message=f"Setting Mario Song time to {args.mario_song_time} seconds.")


    #patches.append("bl", 0xfaa0, "time_graphics_draw_tiles",
    #               message="Intercept time_graphics_draw_tiles 1");
    #patches.append("bl", 0xfbd6, "time_graphics_draw_tiles",
    #               message="Intercept time_graphics_draw_tiles 1");

    if args.slim:
        # TODO: remove the 5 sleep images

        mario_song_len = 0x85e40  # 548,416 bytes
        # This isn't really necessary, but we keep it here because its more explicit.
        patches.append("replace", 0x9001_2D44, b"\x00" * (mario_song_len),
                       message="Erasing Mario Song")
        # Note, bytes starting at 0x90012ca4 leading up to the mario song
        # are also empty.

        # Each tile is 16x16 pixels, stored as 256 bytes in row-major form.
        # These index into a palette. TODO: where is the palette
        patches.append("move", 0x9009_8b84, -mario_song_len, size=0x1_0000,
                       message="Moving custom clock graphics.")
        patches.append("add", 0x7350, -mario_song_len, size=4,
                       message="Update custom clock graphics references")

        # Note: the clock uses a different palette; this palette only applies
        # to ingame Super Mario Bros 1 & 2
        patches.append("move", 0x900a_8b84, -mario_song_len, size=192,
                       message="Move NES emulator palette.")
        patches.append("add", 0xb720, -mario_song_len, size=4,
                       message="Update NES emulator palette references.")

        # Note: UNKNOWN* represents a block of data that i haven't decoded
        # yet. If you know what the block of data is, please let me know!
        patches.append("move", 0x900a_8c44, -mario_song_len, size=8352,
                       message="Move UNKNOWN1")
        patches.append("add", 0xbc44, -mario_song_len, size=4,
                       message=f"Update UNKNOWN1 references")

        #patches.append("move", 0x900a_ace4, -mario_song_len, size=9088,
        patches.append("move", 0x900a_ace4, -mario_song_len, size=0x3f00,
                       message="Move GAME menu icons")
        patches.append("add", 0xcea8, -mario_song_len, size=4,
                       message=f"Update GAME menu icons references")
        # I'm not sure when this is used
        patches.append("add", 0xd2f8, -mario_song_len, size=4,
                       message=f"Update GAME menu icons references UNKNOWN")


        patches.append("move", 0x900a_ebe4, -mario_song_len, size=116,
                       message="Move menu stuff (icons? meta?)")
        references = [
            0x0_d010,
            0x0_d004,
            0x0_d2d8,
            0x0_d2dc,
            0x0_d2f4,
            0x0_d2f0,
        ]
        for i, reference in enumerate(references):
            patches.append("add", reference, -mario_song_len, size=4,
                           message=f"Update menu references {i} at {hex(reference)}")


        # I think the memcpy code should only be 65536 long.
        # stock firmware copies 122_880, like halfway into the mario juggling pic
        patches.append("move", 0x900a_ec58, -mario_song_len, size=0x1_0000,
                       message="Move mario 2 rom")
        patches.append("ks_thumb", 0x6a0a, "mov.w r2, #0x10000", size=4,
                       message="Fix bug? Mario 2 ROM is only 65536 long.")
        patches.append("ks_thumb", 0x6a1e, "mov.w r3, #0x10000", size=4,
                       message="Fix bug? Mario 2 ROM is only 65536 long.")
        patches.append("add", 0x0_7374, -mario_song_len, size=4,
                       message=f"Update Mario 2 ROM reference")

        # Not sure what this data is
        patches.append("move", 0x900bec58, -mario_song_len, size=8 * 2,
                       message="Two sets of uint8_t[8]. Not sure what they represent.")
        patches.append("add", 0x1_0964, -mario_song_len, size=4,
                       message="Two sets of uint8_t[8]. Not sure what they represent.")



        # These somehow describe the time scenes (impacts how all background is drawn)
        patches.append("move", 0x900bec68, -mario_song_len, size=320,
                       message="Time generic scene [0600, 1700)")
        patches.append("move", 0x900beda8, -mario_song_len, size=320,
                       message="Time generic scene [1800, 0400)")
        patches.append("move", 0x900beee8, -mario_song_len, size=320,
                        message="Time underwater scene (between 1200 and 2400 at XX:30)")
        patches.append("move", 0x900bf028, -mario_song_len, size=320)
        patches.append("move", 0x900bf168, -mario_song_len, size=320,
                       message="Time dawn scene [0500, 0600)")
        #               message="Underground coin bonus scene (between 0000 and 1200 at XX:30)")

        # These might be 8x8 2-bpp sprites?
        eight_bytes_start = 0x900bf2a8
        eight_bytes_end   = 0x900bf410
        eight_bytes_len = eight_bytes_end - eight_bytes_start
        for addr in range(eight_bytes_start, eight_bytes_end, 8):
            patches.append("move", addr, -mario_song_len, size=8)


        # IDK what this is.
        patches.append("move", 0x900bf410, -mario_song_len, size=144)
        patches.append("add", 0x1_658c, -mario_song_len, size=4)


        # This table is related to time events.
        lookup_table_start = 0x900b_f4a0
        lookup_table_end   = 0x900b_f838
        lookup_table_len   = lookup_table_end - lookup_table_start  # 920
        def cond(addr):
            # Return True if it's beyond the mario song addr
            # TODO: this ending addr is just until we successfullly move other stuff.
            #return 0x9001_2D44 <= addr < eight_bytes_end
            return 0x9001_2D44 <= addr < 0x900bf950
        for addr in range(lookup_table_start, lookup_table_end, 4):
            patches.append("add", addr, -mario_song_len, size=4, cond=cond)
        # Now move the table
        patches.append("move", lookup_table_start, -mario_song_len, size=lookup_table_len,
                       message="Moving event lookup table")
        patches.append("add", 0xdf88, -mario_song_len, size=4,
                       message="Updating event lookup table reference")


        patches.append("copy", 0x900bf838, -mario_song_len, size=280,)
        patches.append("add", 0xe8f8, -mario_song_len, size=4)
        patches.append("add", 0xf4ec, -mario_song_len, size=4)
        patches.append("add", 0xf4f8, -mario_song_len, size=4)
        patches.append("add", 0x10098, -mario_song_len, size=4)
        patches.append("add", 0x105b0, -mario_song_len, size=4)


        patches.append("copy", 0x900bf950, -mario_song_len, size=180,)
        patches.append("add", 0xe2e4, -mario_song_len, size=4)
        patches.append("add", 0xf4fc, -mario_song_len, size=4)


        patches.append("copy", 0x900bfa04, -mario_song_len, size=8,)
        patches.append("add", 0x1_6590, -mario_song_len, size=4)

        # EVERYTHING IS GOOD UP TO HERE

        # Need to figure out code sections in extflash before proceeding
        #patches.append("copy", 0x900bfa0c, -mario_song_len, size=784,)
        #patches.append("add", 0x1_0f9c, -mario_song_len, size=4)


        #patches.append("add", , -mario_song_len, size=4)
        if False:
            patches.append("copy", 0x900a_ec58, -mario_song_len, size=93_344,
                           message="Move mario 2 rom plus other stuff")
            unknown_references = [
                0x0_7374,  # Pointer to mario 2 rom (65,536 bytes)
                0x1_0964,
                0x1_658c,
                0x0_df88,
                0x0_e8f8,
                0x0_f4ec,
                0x1_0098,  # new
                0x1_05b0,
                0x0_f4f8,
                0x0_e2e4,
                0x0_f4fc,
                0x1_6590,
                0x1_0f9c,

                # weird thunk startup stuff? Thes would be good to check with BPs
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

                # old again
                0x0_43ec,
                0x0_459c,
                0x0_4594,

                # image references
                #0x01097c,  # 0x900c58f8 mario sleeping
                #0x010988,  # 0x900cd858 mario juggling
                #0x01098c,  # 0x900d6c78 bowser sleeping
                #0x010984,  # 0x900e16f8 mario and luigi eating pizza
                #0x010980,  # 0x900ec318 minions sleeping
            ]
            for i, reference in enumerate(unknown_references):
                patches.append("add", reference, -mario_song_len, size=4,
                               message=f"Update UNKNOWN references {i} at {hex(reference)}")




        # Images Notes:
        #    * In-between images are just zeros.
        # start: 0x900C_58F8   end: 0x900C_D83F    mario sleeping
        # start: 0x900C_D858   end: 0x900D_6C65    mario juggling
        # start: 0x900D_6C78   end: 0x900E_16E2    bowser sleeping
        # start: 0x900E_16F8   end: 0x900E_C301    mario and luigi eating pizza
        # start: 0x900E_C318   end: 0x900F_4D04    minions sleeping
        #          zero_padded_end: 0x900f_4d18
        # Total Image Length: 193_568 bytes


        if False:
            patches.append("replace", 0x900a_8b84, b"\xFF" * 192,
                           message="NES palette")

        if False:
            patches.append("replace", 0x900a_8c44, b"\xFF" * 3072,
                           message="")


        # The last 2 4096 byte blocks represent something in settings.
        #patches.append("replace", 0x900f_e000, b"\xFF" * 0x50,
        #               message="erase some settings?")
        #patches.append("replace", 0x900f_f000, b"\xFF" * 0x50,
        #               message="erasure causes first startup")

        #patches.append("replace", 0xac00, 10, size=1)

    return patches
