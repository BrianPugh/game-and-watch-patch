#!/usr/bin/env python3
"""Boot the patched Zelda firmware in GWemu and launch every game.

Boots ``build/internal_flash_patched.bin`` + ``build/external_flash_patched.bin``
in a headless GWemu, leaves the first-boot PRESS TIME screen, then for each
game-select variant (LoZ1/LoZ2 EN/JP, LA EN/FR/DE/JP, Vermin, Timer) opens the
menu, selects it, checks ``g_app_id``, lets it run, screenshots it, and goes
back to the clock with TIME. A launch that faults or hangs stops the polls and
fails the run; a frame with almost no colors (black or blank screen) fails its
check.

Requires gwprov and a GWemu binary; see tools/emu/README.md.

Usage:
    python tools/emu/zelda_menu_test.py --gwemu-bin /path/to/gwemu [--only la_en,timer]
"""

import argparse
import sys
import time
from pathlib import Path

from emu_driver import REPO, Driver, check, make_bank2, make_profile
from gwprov.gwemu_manager import instances, start_instance, stop_instance
from PIL import Image

# Instruction after `bl read_buttons` in main_loop_tick (0x0800fe54, which the
# patch points at our read_buttons; see tools/re/zelda.syms).
READ_BUTTONS_RETURN = 0x0800FE58

APP_ID = 0x2000AB9D  # g_app_id
APP_FIRST_BOOT, APP_CLOCK, APP_MENU = 2, 4, 0x12
UI_STATE = 0x2001A7BC
UI_ROW = UI_STATE + 0x2A  # u8 row cursor
UI_VARIANT = UI_STATE + 0x2D  # u8 chosen variant of each row, stride 2

# (name, menu row, variant id, g_app_id); rows are menu_rows in zelda.syms.
GAMES = [
    ("loz1_en", 0, 0, 6),
    ("loz1_jp", 0, 1, 7),
    ("loz2_en", 1, 2, 8),
    ("loz2_jp", 1, 3, 9),
    ("la_en", 2, 4, 11),
    ("la_fr", 2, 5, 12),
    ("la_de", 2, 6, 13),
    ("la_jp", 2, 7, 14),
    ("vermin", 3, 8, 15),
    ("timer", 4, 9, 5),
]


def colors(path: Path) -> int:
    with Image.open(path) as im:
        return len(im.convert("RGB").getcolors(1 << 20) or ())


def app(d: Driver) -> int:
    return d.read8(APP_ID)


def to_clock(d: Driver):
    d.press_until("TIME", lambda: app(d) == APP_CLOCK, "the clock")


def launch(d: Driver, row: int, variant: int, app_id: int):
    d.press_until("GAME", lambda: app(d) == APP_MENU, "the game-select menu")
    while (cur := d.read8(UI_ROW)) != row:
        d.press_until(
            "DOWN" if cur < row else "UP",
            lambda c=cur: d.read8(UI_ROW) != c,
            "the row cursor to move",
        )
    selected = UI_VARIANT + 2 * row
    while (cur := d.read8(selected)) != variant:
        d.press_until(
            "RIGHT" if cur < variant else "LEFT",
            lambda c=cur: d.read8(selected) != c,
            "the variant to change",
        )
    d.press_until("A", lambda: app(d) == app_id, f"g_app_id {app_id}")


def run(d: Driver, games, run_frames: int) -> bool:
    ok = True
    d.frames("", 30)
    ok &= check(app(d) == APP_FIRST_BOOT, f"first-boot screen (g_app_id={app(d)})")
    d.shot("00_first_boot")
    to_clock(d)
    d.frames("", 60)
    d.shot("01_clock")
    ok &= check(colors(d.out_dir / "01_clock.png") > 8, "clock is drawn")

    for i, (name, row, variant, app_id) in enumerate(games, 2):
        try:
            launch(d, row, variant, app_id)
            d.frames("", run_frames)
            shot = f"{i:02d}_{name}"
            d.shot(shot)
            n = colors(d.out_dir / f"{shot}.png")
            ok &= check(
                app(d) == app_id and n > 8,
                f"{name} runs (g_app_id={app_id}, {n} colors)",
            )
            to_clock(d)
        except RuntimeError as e:
            ok &= check(False, f"{name}: {e}")
            break  # The firmware is likely stuck; later checks would only time out.
    return ok


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--gwemu-bin", required=True, type=Path, help="Path to the GWemu executable."
    )
    parser.add_argument(
        "--only",
        help="Comma-separated games to launch (default: all): "
        + ",".join(g[0] for g in GAMES),
    )
    parser.add_argument(
        "--run-frames", type=int, default=300, help="Frames to run each game."
    )
    parser.add_argument("--work-dir", type=Path, default=REPO / "build/emu")
    parser.add_argument(
        "--internal", type=Path, default=REPO / "build/internal_flash_patched.bin"
    )
    parser.add_argument(
        "--external", type=Path, default=REPO / "build/external_flash_patched.bin"
    )
    args = parser.parse_args()
    games = GAMES
    if args.only:
        names = args.only.split(",")
        unknown = set(names) - {g[0] for g in GAMES}
        if unknown:
            parser.error(f"unknown games: {', '.join(sorted(unknown))}")
        games = [g for g in GAMES if g[0] in names]

    work = args.work_dir.resolve()
    out_dir = work / "zelda_shots"
    out_dir.mkdir(parents=True, exist_ok=True)
    profile = work / "zelda-profile"
    bank2 = work / "bank2_erased.bin"
    make_bank2(bank2, "erased")
    make_profile(
        profile,
        bank2,
        args.internal.resolve(),
        args.external.resolve(),
        model="zelda",
    )

    start_instance(str(profile), headless=True, gwemu_bin=str(args.gwemu_bin.resolve()))
    pid = next(r["pid"] for r in instances() if r["profile"] == str(profile))
    try:
        time.sleep(3)
        ok = run(Driver(pid, out_dir, READ_BUTTONS_RETURN), games, args.run_frames)
    finally:
        stop_instance(pid=pid)
    print(f"Screenshots: {out_dir}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
