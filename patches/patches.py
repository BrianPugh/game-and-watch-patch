from .patch import Patches

def _seconds_to_frames(seconds):
    return int(round(60 * seconds))

def add_patch_args(parser):
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

    if args.mario_song_time:
        mario_song_frames = _seconds_to_frames(args.mario_song_time)
        patches.append("ks_thumb", 0x6fc4, f"cmp.w r0, #{mario_song_frames}", size=4,
                       message=f"Setting Mario Song time to {args.mario_song_time} seconds.")


    if args.slim:
        # TODO: remove the 5 sleep images
        patches.append("replace", 0x6C40, 0x91, size=1,
                       message=f"Disable sleep timer")

        patches.append("replace", 0x9001_2D44, b"\xFF" * (0x85e40),
                       message="Erasing Mario Song")
        # Right after this chunk is some graphics for the clock
        # 0x97d44

        # We'll need to move this palette location.
        # NES palette at 0xA8B84
        if False:
            patches.append("replace", 0x9000_a8b84, b"\xFF" * 192,
                           message="NES palette")

        if False:
            patches.append("replace", 0x9000_a8c44, b"\xFF" * 3072,
                           message="")


        # The last 2 4096 byte blocks represent something in settings.
        patches.append("replace", 0x900f_e000, b"\xFF" * 0x50,
                       message="erase some settings?")
        #patches.append("replace", 0x900f_f000, b"\xFF" * 0x50,
        #               message="erasure causes first startup")

        #patches.append("replace", 0xac00, 10, size=1)

    return patches
