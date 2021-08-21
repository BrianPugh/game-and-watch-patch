from .patch import Patches

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
        raise NotImplementedError
    return patches
