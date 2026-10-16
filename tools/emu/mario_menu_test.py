#!/usr/bin/env python3
"""Screenshot-test the Mario game-select menu in GWemu.

Boots ``build/internal_flash_patched.bin`` + ``build/external_flash_patched.bin``
(build with ``--game-select-dual-boot``) in a headless GWemu, drives the menu
with frame-accurate button injection, saves screenshots, and checks behavior:

* ``--bank2 spin`` (default): bank 2 holds a tiny valid image (``b .`` at
  0x08100008). The DUAL BOOT entry must be shown, and selecting it must leave
  the CPU spinning in bank 2.
* ``--bank2 erased``: the entry must be hidden; DOWN from BALL wraps to SMB1.

Requires gwprov (https://github.com/slash-proc/gwprov) and a GWemu binary
(https://github.com/slash-proc/gwemu/releases). See tools/emu/README.md.

Usage:
    python tools/emu/mario_menu_test.py --gwemu-bin /path/to/gwemu [--bank2 erased]
"""

import argparse
import sys
import time
from pathlib import Path

from emu_driver import REPO, Driver, check, make_bank2, make_profile
from gwprov.gwemu_manager import instances, start_instance, stop_instance

# Instruction after `bl read_buttons` in main_loop_tick (see tools/re/mario.syms).
# Breaking here and overwriting r0 replaces the frame's button state.
READ_BUTTONS_RETURN = 0x08006B56

GNW_MODE = 0x20001044
STATE_TRANSITION = 0x2000103D  # u8; 0 = idle
UI_STATE = 0x20010694  # u8; 0 = no overlay, 5 = overlay open
UI_GAME_SELECT_CHOICE = 0x20010698
BANK2 = 0x08100000
BANK2_SPIN_PC = BANK2 + 8
CHOICE_SMB1, CHOICE_DUAL_BOOT = 0x10, 0x100


def run(driver: Driver, bank2: str) -> bool:
    # GWemu reports some buttons as held when idle (e.g. TIME on 0.0.23), so
    # Driver injects every frame's input; a single un-driven poll would see
    # the "release" of the stuck button and register it as a press.
    driver.frames("", 30)
    driver.tap("TIME", settle=30)  # Leave the first-boot "PRESS TIME" screen.
    driver.wait_until(
        lambda: driver.read8(STATE_TRANSITION) == 0 and driver.read8(UI_STATE) == 0,
        "the clock",
    )
    driver.shot("01_clock")
    # Retry only until the overlay reacts; a second GAME while it slides in
    # would close it again.
    driver.press_until("GAME", lambda: driver.read8(UI_STATE) != 0, "the overlay")
    driver.wait_until(lambda: driver.read8(UI_STATE) == 5, "the game-select menu")
    driver.shot("02_menu")

    ok = True
    for i in range(3):
        before = driver.read16(UI_GAME_SELECT_CHOICE)
        driver.press_until(
            "DOWN",
            lambda: driver.read16(UI_GAME_SELECT_CHOICE) != before,
            "the cursor to move",
        )
        driver.shot(f"03_down{i + 1}")
    choice = driver.read16(UI_GAME_SELECT_CHOICE)

    if bank2 == "spin":
        ok &= check(
            choice == CHOICE_DUAL_BOOT,
            f"DUAL BOOT selected after 3x DOWN (choice=0x{choice:X})",
        )
        driver.last_frame("A")  # The firmware resets during this frame.
        time.sleep(1)
        pc = driver.pc()
        ok &= check(
            pc & ~1 == BANK2_SPIN_PC, f"A on DUAL BOOT runs bank 2 (pc=0x{pc:08X})"
        )
    else:
        ok &= check(
            choice == CHOICE_SMB1,
            f"entry hidden; 3x DOWN wraps to SMB1 (choice=0x{choice:X})",
        )
        driver.tap("B")
        mode = driver.read16(GNW_MODE)
        ok &= check(
            mode & 0xC2 == 0x02,
            f"B closes the menu back to the clock (gnw_mode=0x{mode:X})",
        )
    return ok


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--gwemu-bin", required=True, type=Path, help="Path to the GWemu executable."
    )
    parser.add_argument("--bank2", choices=("spin", "erased"), default="spin")
    parser.add_argument("--work-dir", type=Path, default=REPO / "build/emu")
    parser.add_argument(
        "--internal", type=Path, default=REPO / "build/internal_flash_patched.bin"
    )
    parser.add_argument(
        "--external", type=Path, default=REPO / "build/external_flash_patched.bin"
    )
    parser.add_argument(
        "--bank2-image",
        type=Path,
        help="Bank-2 contents (e.g. build/internal_flash_patched_bank2.bin from "
        "--relocate-stock); the --bank2 header goes over its first bytes.",
    )
    parser.add_argument(
        "--stock-offset",
        type=lambda s: int(s, 0),
        default=0,
        help="How far the stock firmware was relocated (--relocate-stock minus 0x08000000).",
    )
    args = parser.parse_args()

    work = args.work_dir.resolve()
    out_dir = work / f"shots_{args.bank2}"
    out_dir.mkdir(parents=True, exist_ok=True)
    profile = work / "mario-profile"
    bank2 = work / f"bank2_{args.bank2}.bin"
    make_bank2(bank2, args.bank2, args.bank2_image, BANK2_SPIN_PC)
    make_profile(profile, bank2, args.internal.resolve(), args.external.resolve())

    start_instance(str(profile), headless=True, gwemu_bin=str(args.gwemu_bin.resolve()))
    pid = next(r["pid"] for r in instances() if r["profile"] == str(profile))
    try:
        time.sleep(3)
        ok = run(
            Driver(pid, out_dir, READ_BUTTONS_RETURN + args.stock_offset), args.bank2
        )
    finally:
        stop_instance(pid=pid)
    print(f"Screenshots: {out_dir}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
