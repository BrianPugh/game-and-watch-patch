from .patch import Patches

def parse_patches(args):
    patches = Patches()

    patches.append("replace", 0x4, "bootloader",
                   message="Invoke custom bootloader prior to calling stock Reset_Handler")

    patches.append("bl", 0x6b52, "read_buttons",
                   message="Intercept button presses for macros")

    if args.winbond:
        patches.append("replace", 0x4FAA, 0x0240, size=2)
        patches.append("replace", 0x4FB8, 0xFDBF, size=2)
        patches.append("replace", 0x4FC4, 0x7B43, size=2)
        patches.append("replace", 0x5004, 0x8141, size=2)
        patches.append("replace", 0x5005, 0x0706, size=2)
        patches.append("replace", 0x500E, 0x9050, size=2)
        patches.append("replace", 0x500F, 0x0706, size=2)
        patches.append("replace", 0x755E, 0x3238, size=2)
        patches.append("replace", 0x7562, 0x8040, size=2)
        patches.append("replace", 0x7776, 0x3505, size=2)
        patches.append("replace", 0x7796, 0x000D, size=2)
        patches.append("replace", 0x7797, 0x20F1, size=2)
        patches.append("replace", 0x7798, 0x8D01, size=2)
        patches.append("replace", 0x7799, 0xF801, size=2)
        patches.append("replace", 0x779A, 0x0020, size=2)
        patches.append("replace", 0x779B, 0x0046, size=2)
        patches.append("replace", 0x779C, 0xC000, size=2)
        patches.append("replace", 0x779D, 0x46F0, size=2)
        patches.append("replace", 0x779E, 0xC03C, size=2)
        patches.append("replace", 0x779F, 0x46F8, size=2)
        patches.append("replace", 0x77A0, 0xC000, size=2)
        patches.append("replace", 0x77A1, 0x4628, size=2)
        patches.append("replace", 0x77A2, 0xC009, size=2)
        patches.append("replace", 0x77A3, 0x46D1, size=2)
        patches.append("replace", 0x77AA, 0x0100, size=2)

        if args.winbond == 1:
            patches.append("replace", 0xA41E, 0x1414, size=2, message="Winbond 1MB")
        elif args.winbond == 2:
            patches.append("replace", 0xA41E, 0x1514, size=2, message="Winbond 2MB")
        elif args.winbond == 4:
            patches.append("replace", 0xA41E, 0x1614, size=2, message="Winbond 4MB")
        elif args.winbond == 8:
            patches.append("replace", 0xA41E, 0x1714, size=2, message="Winbond 8MB")
        elif args.winbond == 16:
            patches.append("replace", 0xA41E, 0x1814, size=2, message="Winbond 16MB")
        elif args.winbond == 32:
            patches.append("replace", 0xA41E, 0x1914, size=2, message="Winbond 32MB")
        else:
            raise ValueError(f"Cannot handle Winbond flash size {args.winbond}MB")

    return patches
