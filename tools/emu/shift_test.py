"""Play every Mario mode on a relocated stock firmware in GWemu.

Images: ``tools/re/shift_image.py`` (stock only, default) or a patched build
made with ``--relocate-stock`` (pass ``--bank1/--bank2/--external``). Plays
boot, clock, SMB1, BALL, pause, clock and SMB2 with button injection at the
shifted ``read_buttons`` return, waiting on the firmware's own state
(transition, overlay, ``gnw_mode``) instead of fixed frame counts. Saves a
screenshot per phase and fails if a phase doesn't reach its mode, stops
reaching the injection site (a fault or hang), or the CPU is seen in the stock
code's old place.

Usage:
    python tools/emu/shift_test.py --gwemu-bin /path/to/gwemu
    python tools/emu/shift_test.py --gwemu-bin /path/to/gwemu --delta 0x120000 \\
        --bank1 build/internal_flash_patched.bin \\
        --bank2 build/internal_flash_patched_bank2.bin \\
        --external build/external_flash_patched.bin
"""

import argparse
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
import mario_menu_test as menu  # noqa: E402
from emu_driver import Driver, check, make_profile  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
DELTA = 0x10_0000
SMB1, SMB2, BALL, CLOCK = 0x10, 0x20, 0x08, 0x02


def idle(d):
    return d.read8(menu.STATE_TRANSITION) == 0 and d.read8(menu.UI_STATE) == 0


def is_mode(mode):
    return lambda m: m == mode


def play(d, mode):
    d.wait_until(lambda: idle(d), "idle")
    # Retry only until the overlay reacts; another press while it opens closes it.
    d.press_until("GAME", lambda: d.read8(menu.UI_STATE) != 0, "the overlay")
    d.wait_until(lambda: d.read8(menu.UI_STATE) == 5, "the game-select menu")
    for _ in range(4):  # The cursor starts on the current game.
        if d.read16(menu.UI_GAME_SELECT_CHOICE) == mode:
            break
        d.tap("DOWN")
    d.press_until(
        "A", lambda: d.read16(menu.GNW_MODE) == mode and idle(d), f"gnw_mode 0x{mode:X}"
    )
    for _ in range(10):
        d.tap("A", settle=30)
    for _ in range(30):
        d.frames("RIGHT", 20)
        d.frames("RIGHT+A", 10)


def to_clock(d, first_boot=False):
    if first_boot:
        d.frames("", 30)
    else:
        d.wait_until(lambda: idle(d), "idle")
    d.press_until(
        "TIME", lambda: idle(d) and d.read16(menu.GNW_MODE) & 0x42 == CLOCK, "the clock"
    )
    d.frames("", 300)


def pause(d):
    d.wait_until(lambda: idle(d), "idle")
    d.press_until("PAUSE", lambda: d.read8(menu.UI_STATE) != 0, "the overlay")
    d.wait_until(lambda: d.read8(menu.UI_STATE) == 5, "the pause menu")
    for b in ("DOWN", "RIGHT", "UP", "LEFT"):
        d.tap(b)
    d.tap("B", settle=10)
    d.wait_until(lambda: idle(d), "the pause menu to close")


# (name, step, check of gnw_mode afterwards)
PHASES = [
    ("boot", lambda d: to_clock(d, first_boot=True), lambda m: m & 0x42 == CLOCK),
    ("smb1", lambda d: play(d, SMB1), is_mode(SMB1)),
    ("ball", lambda d: play(d, BALL), is_mode(BALL)),
    ("pause", pause, is_mode(BALL)),
    ("clock_again", to_clock, lambda m: m & 0x42 == CLOCK),
    ("smb2", lambda d: play(d, SMB2), is_mode(SMB2)),
]


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--gwemu-bin", required=True, type=Path, help="Path to the GWemu executable."
    )
    parser.add_argument(
        "--images",
        type=Path,
        default=REPO / "build/re/shift",
        help="Directory with bank1.bin, bank2.bin and external.bin.",
    )
    parser.add_argument("--bank1", type=Path, help="Overrides <images>/bank1.bin.")
    parser.add_argument("--bank2", type=Path, help="Overrides <images>/bank2.bin.")
    parser.add_argument(
        "--external", type=Path, help="Overrides <images>/external.bin."
    )
    parser.add_argument("--work-dir", type=Path, default=REPO / "build/emu")
    parser.add_argument(
        "--delta",
        type=lambda s: int(s, 0),
        default=DELTA,
        help="Shift of the image (0 runs unshifted images, e.g. to test the harness).",
    )
    args = parser.parse_args()

    work = args.work_dir.resolve()
    out_dir = work / "shift"
    out_dir.mkdir(parents=True, exist_ok=True)
    profile = work / "mario-shift-profile"
    images = args.images.resolve()
    bank1 = (args.bank1 or images / "bank1.bin").resolve()
    bank2 = (args.bank2 or images / "bank2.bin").resolve()
    external = (args.external or images / "external.bin").resolve()
    make_profile(profile, bank2, bank1, external)

    start_instance(str(profile), headless=True, gwemu_bin=str(args.gwemu_bin.resolve()))
    pid = next(r["pid"] for r in instances() if r["profile"] == str(profile))
    ok = True
    try:
        time.sleep(3)
        driver = Driver(pid, out_dir, menu.READ_BUTTONS_RETURN + args.delta)
        for name, step, mode_ok in PHASES:
            try:
                step(driver)
            except RuntimeError as e:
                pc = driver.pc()
                screenshot_instance(pid=pid, output=out_dir / f"{name}_FAIL.png")
                print(f"FAIL: {name}: {e} (pc=0x{pc:08X})")
                ok = False
                break
            driver.shot(name)
            mode = driver.read16(menu.GNW_MODE)
            pc = driver.pc()
            # The stock code's old place (below the init table at 0x0801807c).
            in_old = args.delta and 0x0800_0000 <= pc < 0x0801_807C
            ok &= check(
                mode_ok(mode) and not in_old,
                f"{name}: gnw_mode=0x{mode:X}, pc=0x{pc:08X}",
            )
    finally:
        stop_instance(pid=pid)
    print(f"Screenshots: {out_dir}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
