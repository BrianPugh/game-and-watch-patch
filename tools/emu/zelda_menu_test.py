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

``--no-la`` runs a ``--no-la`` build: no LA games, VERMIN and TIMER a row
up, and 4 stock rows (5 with DUAL BOOT, so the window never scrolls and the
carousel check uses LoZ1's row).

``--amalgam`` runs a ``--device=amalgam`` build: it boots Mario, picks Mario's
switch (ZELDA) entry, and runs the checks on the Zelda it resets into; its
switch (MARIO) row must then reset back into Mario. Each switch checks the
selection main.c writes to the TAMP backup register. GWemu clears that
register on reset (hardware keeps it), so the harness writes it back when the
bootloader starts. With ``--dual-boot`` too (a ``--game-select-dual-boot``
build), both menus must show the DUAL BOOT entry after the switch (``spin``)
or hide it (``erased``), and ``spin`` ends with A on Zelda's DUAL BOOT row
instead of the switch back. ``--sd-bootloader`` puts the ``--dual-boot``
image where the SD bootloader goes (0x08032000) instead of bank 2.
"""

import argparse
import struct
import sys
import time
from pathlib import Path

import mario_menu_test as mario
from elftools.elf.elffile import ELFFile
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
BANK2_SPIN_PC = 0x0810_0008
# --sd-bootloader: the dual-boot target's place in bank 1.
SD_BOOTLOADER_OFFSET = 0x3_2000
SD_SPIN_PC = 0x0800_0008 + SD_BOOTLOADER_OFFSET

# --amalgam: Zelda's stock code below its factory test (read_buttons' return
# included) moves up and closes the gaps the patcher frees there
# (patches/amalgam.py), so the harness looks its new place up in
# build/patched.elf; main.c keeps the selection in AMALGAM_BOOT_REG.
TAMP_BKP31R = 0x5800_457C
BOOT_MARIO, BOOT_ZELDA = 0, 0x444C_455A  # "ZELD"
SCB_VTOR = 0xE000_ED08
# Mario's game-select choices (game_select_mario.c): the stock entries, then
# the amalgam's custom ones, the switch and DUAL BOOT.
MARIO_SMB1, MARIO_SMB2, MARIO_BALL = 0x10, 0x20, 0x08
MARIO_SWITCH, MARIO_DUAL_BOOT = 0x100, 0x200

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
# --no-la (patches/zelda.py _game_select_drop_la_row): VERMIN and TIMER move up.
GAMES_NO_LA = [
    ("loz1_en", 0, 0, 6),
    ("loz1_jp", 0, 1, 7),
    ("loz2_en", 1, 2, 8),
    ("loz2_jp", 1, 3, 9),
    ("vermin", 2, 8, 15),
    ("timer", 3, 9, 5),
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


# DOWN presses in row_wrap: past the most rows (5 stock and 2 custom).
WRAP_PRESSES = 8


def row_wrap(d: Driver) -> list:
    """Open the menu and press DOWN WRAP_PRESSES times from the first row;
    returns the rows the cursor visited."""
    d.press_until("GAME", lambda: app(d) == APP_MENU, "the game-select menu")
    while d.read8(UI_ROW) != 0:
        step(d, "UP")
    rows = []
    for _ in range(WRAP_PRESSES):
        step(d, "DOWN")
        rows.append(d.read8(UI_ROW))
    return rows


def wraps(n: int) -> list:
    return [(i + 1) % n for i in range(WRAP_PRESSES)]


def dual_boot(d: Driver, bank2: str, n_stock: int, amalgam: bool, spin_pc: int) -> bool:
    """The custom rows after the stock ones: the amalgam's switch, then DUAL
    BOOT (``bank2`` "spin"; "erased" hides it, None: not built)."""
    ok = True
    switch_row = n_stock if amalgam else None
    dual_boot_row = n_stock + amalgam if bank2 == "spin" else None
    n = n_stock + amalgam + (bank2 == "spin")
    rows = row_wrap(d)
    if n == n_stock:
        d.shot("90_menu_no_dual_boot")
        return check(rows == wraps(n_stock), f"DUAL BOOT row hidden (DOWN: {rows})")
    ok &= check(rows == wraps(n), f"{n} rows, DOWN wraps (DOWN: {rows})")
    while d.read8(UI_ROW) != 0:
        step(d, "UP")
    step(d, "UP")
    ok &= check(d.read8(UI_ROW) == n - 1, "UP from the first row wraps to the last")
    d.shot("90_menu_custom")

    # Everything from the slide offset on, past the blink counter (+0x08).
    before = d.read(UI_STATE + 0x28, 0x28)
    d.tap("LEFT")
    d.tap("RIGHT")
    after = d.read(UI_STATE + 0x28, 0x28)
    ok &= check(
        before == after and app(d) == APP_MENU, "LEFT/RIGHT do nothing on the last row"
    )

    # Slide a carousel; past 5 rows, LA's, with the window scrolled down.
    row = 2 if n > 5 else 0
    while d.read8(UI_ROW) != row:
        step(d, "UP")
    variant = d.read8(UI_VARIANT + 2 * row)
    d.press_until(
        "RIGHT",
        lambda: d.read8(UI_VARIANT + 2 * row) != variant,
        f"row {row}'s variant to change",
    )
    d.wait_until(lambda: d.read8(UI_SLIDE) == 0, "the carousel slide")
    d.shot("91_menu_carousel")
    ok &= check(app(d) == APP_MENU, f"carousel slides ({n} rows)")

    if dual_boot_row is None:
        while d.read8(UI_ROW) != switch_row:
            step(d, "DOWN")
        ok &= amalgam_switch(d, BOOT_MARIO, mario.READ_BUTTONS_RETURN, 0x0800_0000)
        d.frames("", 30)
        d.shot("92_mario_again")
        return ok
    while d.read8(UI_ROW) != dual_boot_row:
        step(d, "DOWN")
    d.last_frame("A")  # The firmware resets during this frame.
    time.sleep(1)
    pc = d.pc()
    ok &= check(pc & ~1 == spin_pc, f"A on DUAL BOOT runs the target (pc=0x{pc:08X})")
    return ok


def elf_symbol(name: str) -> int:
    with open(REPO / "build/gw_patch.elf", "rb") as f:
        sym = ELFFile(f).get_section_by_name(".symtab").get_symbol_by_name(name)
    return sym[0]["st_value"] & ~1


def final_address(addr: int) -> int:
    """Where Zelda's stock ``addr`` is in the patched image: its function's
    stock_zelda_* symbol in build/patched.elf, plus the offset into it."""
    import importlib.util

    # patches/syms.py alone, without the patches package's dependencies.
    spec = importlib.util.spec_from_file_location("syms", REPO / "patches/syms.py")
    syms = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(syms)
    funcs = syms.stock_functions("zelda")
    name = max((a, n) for n, a in funcs.items() if a & ~1 <= addr)[1]
    with open(REPO / "build/patched.elf", "rb") as f:
        sym = ELFFile(f).get_section_by_name(".symtab").get_symbol_by_name(name)
    return (sym[0]["st_value"] & ~1) + addr - (funcs[name] & ~1)


def read32(d: Driver, addr: int) -> int:
    return struct.unpack("<I", d.dbg.backend.read_memory(addr, 4))[0]


def amalgam_switch(d: Driver, boot: int, poll_pc: int, vtor: int) -> bool:
    """Press A on the DUAL BOOT entry, check the selection written before the
    reset, and stop at the other firmware's first poll."""
    d.release_to("A", elf_symbol("__NVIC_SystemReset"))
    got = read32(d, TAMP_BKP31R)
    ok = check(got == boot, f"A selects 0x{boot:08X} (TAMP BKP31R=0x{got:08X})")
    d.run_to(elf_symbol("bootloader"))
    d.dbg.backend.write_memory(TAMP_BKP31R, struct.pack("<I", boot))
    d.poll_pc = poll_pc
    d.run_to_poll()
    got = read32(d, SCB_VTOR)
    return ok & check(got == vtor, f"booted with VTOR=0x{got:08X}")


def amalgam_to_zelda(d: Driver, bank2: str) -> bool:
    """Boot Mario, check its game-select entries (DUAL BOOT per ``bank2``, as
    in ``dual_boot``), and switch to Zelda from the menu."""
    d.frames("", 30)
    d.tap("TIME", settle=30)  # Mario's first-boot PRESS TIME screen.
    d.wait_until(
        lambda: d.read8(mario.STATE_TRANSITION) == 0 and d.read8(mario.UI_STATE) == 0,
        "Mario's clock",
    )
    d.press_until("GAME", lambda: d.read8(mario.UI_STATE) != 0, "Mario's overlay")
    d.wait_until(lambda: d.read8(mario.UI_STATE) == 5, "Mario's game-select menu")
    choice = mario.UI_GAME_SELECT_CHOICE

    def down():
        before = d.read16(choice)
        d.press_until("DOWN", lambda: d.read16(choice) != before, "the cursor to move")

    entries = [MARIO_SMB1, MARIO_SMB2, MARIO_BALL, MARIO_SWITCH]
    if bank2 == "spin":
        entries.append(MARIO_DUAL_BOOT)
    while d.read16(choice) != MARIO_SMB1:
        down()
    seen = []
    for _ in range(len(entries)):
        down()
        seen.append(d.read16(choice))
    ok = check(
        seen == entries[1:] + entries[:1],
        f"Mario's {len(entries)} entries wrap (DOWN: {[hex(c) for c in seen]})",
    )
    while d.read16(choice) != MARIO_SWITCH:
        down()
    d.shot("00_mario_menu_zelda")
    return ok & amalgam_switch(
        d, BOOT_ZELDA, final_address(READ_BUTTONS_RETURN), 0x0801_B400
    )


def run(
    d: Driver,
    games,
    run_frames: int,
    bank2: str = None,
    n_stock: int = 5,
    amalgam: bool = False,
    spin_pc: int = BANK2_SPIN_PC,
) -> bool:
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
    try:
        if bank2 or amalgam:
            ok &= dual_boot(d, bank2, n_stock, amalgam, spin_pc)
        else:
            rows = row_wrap(d)
            d.shot("90_menu")
            ok &= check(rows == wraps(n_stock), f"{n_stock} rows (DOWN: {rows})")
    except RuntimeError as e:
        ok &= check(False, f"menu rows: {e}")
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
    parser.add_argument(
        "--amalgam",
        action="store_true",
        help="Check a --device=amalgam build, booting Zelda from Mario (see above).",
    )
    parser.add_argument(
        "--sd-bootloader",
        action="store_true",
        help="The dual-boot target is at 0x08032000 (an --sd-bootloader build).",
    )
    parser.add_argument(
        "--no-la",
        action="store_true",
        help="Check a --no-la build (no LA row; see above).",
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
    games = GAMES_NO_LA if args.no_la else GAMES
    n_stock = 4 if args.no_la else 5
    if args.only:
        names = args.only.split(",")
        unknown = set(names) - {g[0] for g in games}
        if unknown:
            parser.error(f"unknown games: {', '.join(sorted(unknown))}")
        games = [g for g in games if g[0] in names]

    work = args.work_dir.resolve()
    out_dir = work / "zelda_shots"
    out_dir.mkdir(parents=True, exist_ok=True)
    profile = work / "zelda-profile"
    internal = args.internal.resolve()
    spin_pc = BANK2_SPIN_PC
    bank2_kind = args.dual_boot or "erased"
    if args.sd_bootloader:
        spin_pc = SD_SPIN_PC
        sd_bank1 = work / f"bank1_sd_{bank2_kind}.bin"
        make_bank2(sd_bank1, bank2_kind, internal, spin_pc, at=SD_BOOTLOADER_OFFSET)
        internal, bank2_kind = sd_bank1, "erased"
    bank2 = work / f"bank2_{bank2_kind}.bin"
    make_bank2(bank2, bank2_kind, args.bank2_image, BANK2_SPIN_PC)
    make_profile(profile, bank2, internal, args.external.resolve(), model="zelda")

    start_instance(str(profile), headless=True, gwemu_bin=str(args.gwemu_bin.resolve()))
    pid = next(r["pid"] for r in instances() if r["profile"] == str(profile))
    try:
        time.sleep(3)
        if args.amalgam:
            d = Driver(pid, out_dir, mario.READ_BUTTONS_RETURN)
            ok = amalgam_to_zelda(d, args.dual_boot)
            if ok:
                ok &= run(
                    d, games, args.run_frames, args.dual_boot, n_stock, True, spin_pc
                )
        else:
            poll_pc = READ_BUTTONS_RETURN + args.stock_offset
            d = Driver(pid, out_dir, poll_pc)
            ok = run(
                d, games, args.run_frames, args.dual_boot, n_stock, spin_pc=spin_pc
            )
    finally:
        stop_instance(pid=pid)
    print(f"Screenshots: {out_dir}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
