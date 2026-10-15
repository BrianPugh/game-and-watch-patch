"""Boot the shifted stock image from ``tools/re/shift_image.py`` in GWemu.

Bank 1 holds only the shifted vector table (the rest is ``udf``); bank 2 holds
the whole stock image relocated to 0x08100000. Plays the same phases as
``coverage.py`` (boot, clock, SMB1, BALL, pause, clock, SMB2) with button
injection at the shifted ``read_buttons`` return, saves a screenshot per
phase, and fails if a phase stops reaching that site (a fault or hang) or if
the CPU is ever seen executing bank 1.

Usage:
    python tools/emu/shift_test.py --gwemu-bin /path/to/gwemu
"""

import argparse
import struct
import sys
import time
from pathlib import Path

from gwprov.gwemu_manager import (
    instances,
    screenshot_instance,
    start_instance,
    stop_instance,
)

sys.path.insert(0, str(Path(__file__).resolve().parent))
import coverage  # noqa: E402
import mario_menu_test as menu  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
DELTA = 0x10_0000


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--gwemu-bin", required=True, type=Path, help="Path to the GWemu executable."
    )
    parser.add_argument("--images", type=Path, default=REPO / "build/re/shift")
    parser.add_argument("--work-dir", type=Path, default=REPO / "build/emu")
    parser.add_argument(
        "--delta",
        type=lambda s: int(s, 0),
        default=DELTA,
        help="Shift of the image (0 runs unshifted images, e.g. to test the harness).",
    )
    args = parser.parse_args()

    menu.READ_BUTTONS_RETURN += args.delta  # Driver looks this up on every call.
    work = args.work_dir.resolve()
    out_dir = work / "shift"
    out_dir.mkdir(parents=True, exist_ok=True)
    profile = work / "mario-shift-profile"
    images = args.images.resolve()
    menu.make_profile(
        profile, images / "bank2.bin", images / "bank1.bin", images / "external.bin"
    )

    start_instance(str(profile), headless=True, gwemu_bin=str(args.gwemu_bin.resolve()))
    pid = next(r["pid"] for r in instances() if r["profile"] == str(profile))
    ok = True
    try:
        time.sleep(3)
        driver = menu.Driver(pid, out_dir)
        for name, step in coverage.PHASES:
            try:
                step(driver)
            except RuntimeError as e:
                pc = driver.pc()
                screenshot_instance(pid=pid, output=out_dir / f"{name}_FAIL.png")
                print(f"FAIL: {name}: {e} (pc=0x{pc:08X})")
                ok = False
                break
            driver.dbg.run_until(menu.READ_BUTTONS_RETURN, timeout=10)
            screenshot_instance(pid=pid, output=out_dir / f"{name}.png")
            mode = struct.unpack(
                "<H", driver.dbg.backend.read_memory(menu.GNW_MODE, 2)
            )[0]
            driver.dbg.backend.write_register("r0", 0)
            driver.dbg.resume()
            pc = driver.pc()
            in_bank1 = args.delta and 0x0800_0000 <= pc < 0x0810_0000
            ok &= menu.check(
                not in_bank1, f"{name}: gnw_mode=0x{mode:X}, pc=0x{pc:08X}"
            )
    finally:
        stop_instance(pid=pid)
    print(f"Screenshots: {out_dir}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
