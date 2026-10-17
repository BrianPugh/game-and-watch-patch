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

With ``--relocate-stock=0x08120000``, add ``--bank2-image
build/internal_flash_patched_bank2.bin --stock-offset 0x120000``.

For a ``--game-select-dual-boot`` build, ``--dual-boot spin`` puts a tiny
valid image in bank 2 (``b .`` at 0x08100008) and checks the DUAL BOOT row
after the games: 6 rows that wrap, LEFT/RIGHT inert on it, a carousel slide
while the window is scrolled, and A ending with the CPU spinning in bank 2.
``--dual-boot erased`` checks that the row is hidden (5 rows).
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
UI_SLIDE = UI_STATE + 0x2C  # s8 carousel slide direction, 0 when idle
UI_VARIANT = UI_STATE + 0x2D  # u8 chosen variant of each row, stride 2
DUAL_BOOT_ROW = 5
BANK2_SPIN_PC = 0x0810_0008

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


def step(d: Driver, buttons: str):
    cur = d.read8(UI_ROW)
    d.press_until(buttons, lambda: d.read8(UI_ROW) != cur, "the row cursor to move")


def dual_boot(d: Driver, bank2: str) -> bool:
    ok = True
    d.press_until("GAME", lambda: app(d) == APP_MENU, "the game-select menu")
    while d.read8(UI_ROW) != 0:
        step(d, "UP")
    rows = []
    for _ in range(6):
        step(d, "DOWN")
        rows.append(d.read8(UI_ROW))
    if bank2 == "erased":
        d.shot("90_menu_no_dual_boot")
        return check(rows == [1, 2, 3, 4, 0, 1], f"DUAL BOOT row hidden (DOWN: {rows})")
    ok &= check(rows == [1, 2, 3, 4, 5, 0], f"6 rows, DOWN wraps (DOWN: {rows})")
    step(d, "UP")
    ok &= check(
        d.read8(UI_ROW) == DUAL_BOOT_ROW, "UP from the first row wraps to DUAL BOOT"
    )
    d.shot("90_menu_dual_boot")

    # Everything from the slide offset on, past the blink counter (+0x08).
    before = d.read(UI_STATE + 0x28, 0x28)
    d.tap("LEFT")
    d.tap("RIGHT")
    after = d.read(UI_STATE + 0x28, 0x28)
    ok &= check(
        before == after and app(d) == APP_MENU, "LEFT/RIGHT do nothing on DUAL BOOT"
    )

    # Slide LA's carousel with the window scrolled down by a row.
    while d.read8(UI_ROW) != 2:
        step(d, "UP")
    variant = d.read8(UI_VARIANT + 2 * 2)
    d.press_until(
        "RIGHT",
        lambda: d.read8(UI_VARIANT + 2 * 2) != variant,
        "LA's variant to change",
    )
    d.wait_until(lambda: d.read8(UI_SLIDE) == 0, "the carousel slide")
    d.shot("91_menu_scrolled_carousel")
    ok &= check(app(d) == APP_MENU, "carousel slides in the scrolled window")

    while d.read8(UI_ROW) != DUAL_BOOT_ROW:
        step(d, "DOWN")
    d.last_frame("A")  # The firmware resets during this frame.
    time.sleep(1)
    pc = d.pc()
    ok &= check(pc & ~1 == BANK2_SPIN_PC, f"A on DUAL BOOT runs bank 2 (pc=0x{pc:08X})")
    return ok


def run(d: Driver, games, run_frames: int, bank2: str = None) -> bool:
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
            return ok  # The firmware is likely stuck; later checks would only time out.
    if bank2:
        try:
            ok &= dual_boot(d, bank2)
        except RuntimeError as e:
            ok &= check(False, f"dual boot: {e}")
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
    parser.add_argument(
        "--dual-boot",
        choices=("spin", "erased"),
        help="Check a --game-select-dual-boot build with this bank 2 (see above).",
    )
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
        "--relocate-stock); its first 16 bytes are erased.",
    )
    parser.add_argument(
        "--stock-offset",
        type=lambda s: int(s, 0),
        default=0,
        help="How far the stock firmware was relocated (--relocate-stock minus 0x08000000).",
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
    bank2 = work / f"bank2_{args.dual_boot or 'erased'}.bin"
    make_bank2(bank2, args.dual_boot or "erased", args.bank2_image, BANK2_SPIN_PC)
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
        poll_pc = READ_BUTTONS_RETURN + args.stock_offset
        ok = run(Driver(pid, out_dir, poll_pc), games, args.run_frames, args.dual_boot)
    finally:
        stop_instance(pid=pid)
    print(f"Screenshots: {out_dir}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
