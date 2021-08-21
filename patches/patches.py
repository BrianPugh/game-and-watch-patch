from .patch import Patches

def add_patch_args(parser):
    parser.add_argument("--sleep-timeout", type=float, default=None,
                        help="Go to sleep after this many seconds of inactivity.. "
                         "Valid range: [1, 1092]"
                        )
    parser.add_argument("--hard-reset-timeout", type=float, default=None,
                         help="Hold power button for this many seconds to perform hard reset."
                         )


def patch_args_validation(args):
    if args.sleep_timeout and (args.sleep_timeout < 1 or args.sleep_timeout > 1092):
        parser.error("--sleep-timeout must be in range [1, 1092]")


def parse_patches(args):
    patches = Patches()

    patches.append("replace", 0x4, "bootloader",
                   message="Invoke custom bootloader prior to calling stock Reset_Handler")
    patches.append("bl", 0x6b52, "read_buttons",
                   message="Intercept button presses for macros")

    if args.hard_reset_timeout:
        hard_reset_timeout_ms = int(round(args.hard_reset_timeout * 1000))
        patches.append("ks_thumb", 0x9cee, f"movw r1, #{hard_reset_timeout_ms}", size=4,
                       message=f"Hold power button for {hard_reset_timeout_ms} "
                                "milliseconds to perform hard reset.")

    if args.sleep_timeout:
        sleep_timeout_frames = 60 * args.sleep_timeout  # 60 frames-per-second
        patches.append("ks_thumb", 0x6c3c, f"movw r2, #{sleep_timeout_frames}", size=4,
                       message=f"Setting sleep timeout to {args.sleep_timeout} seconds.")

    return patches
