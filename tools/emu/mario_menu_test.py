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
import struct
import subprocess
import sys
import time
from pathlib import Path

from gwprov.debug_shell import DebugSession, GwemuGDBBackend
from gwprov.gwemu_manager import (
    instances,
    screenshot_instance,
    start_instance,
    stop_instance,
)

REPO = Path(__file__).resolve().parents[2]

# Instruction after `bl read_buttons` in main_loop_tick (see tools/re/mario.syms).
# Breaking here and overwriting r0 replaces the frame's button state.
READ_BUTTONS_RETURN = 0x08006B56

GNW_MODE = 0x20001044
UI_GAME_SELECT_CHOICE = 0x20010698
BANK2 = 0x08100000
BANK2_SPIN_PC = BANK2 + 8

BITS = {
    "RIGHT": 0,
    "LEFT": 1,
    "DOWN": 2,
    "UP": 3,
    "B": 6,
    "A": 7,
    "TIME": 8,
    "PAUSE": 9,
    "GAME": 10,
}
CHOICE_SMB1, CHOICE_DUAL_BOOT = 0x10, 0x100


def gwprov(*args):
    exe = Path(sys.executable).parent / "gwprov"
    subprocess.run([str(exe), *map(str, args)], check=True, stdout=subprocess.DEVNULL)


def make_profile(profile: Path, bank2: Path):
    """Stock Mario profile with our patched images swapped in."""
    gwprov("backups", "add", REPO / "binaries")
    if not (profile / "flash-map.yaml").exists():
        gwprov("profile", "create", "-m", profile)
        gwprov("profile", profile, "edit", "remove", "bank1", "Stock Mario")
        gwprov("profile", profile, "edit", "remove", "bank0", "Mario Assets")
        gwprov("profile", profile, "edit", "remove", "bank2", "Erased (0xFF)")
    else:
        for bank, name in (
            ("bank1", "Patched"),
            ("bank0", "Patched"),
            ("bank2", "Test bank2"),
        ):
            try:
                gwprov("profile", profile, "edit", "remove", bank, name)
            except subprocess.CalledProcessError:
                pass
    gwprov(
        "profile",
        profile,
        "edit",
        "add",
        "bank1",
        "Patched",
        REPO / "build/internal_flash_patched.bin",
        "--offset",
        0,
    )
    gwprov(
        "profile",
        profile,
        "edit",
        "add",
        "bank0",
        "Patched",
        REPO / "build/external_flash_patched.bin",
        "--offset",
        0,
    )
    gwprov(
        "profile", profile, "edit", "add", "bank2", "Test bank2", bank2, "--offset", 0
    )


def make_bank2(path: Path, kind: str):
    data = bytearray(b"\xff" * 0x40000)
    if kind == "spin":
        # Initial SP, reset vector -> 0x08100008 (Thumb), then `b .`.
        data[0:8] = struct.pack("<II", 0x2002_0000, BANK2_SPIN_PC | 1)
        data[8:10] = b"\xfe\xe7"
    path.write_bytes(data)


class Driver:
    def __init__(self, pid: int, out_dir: Path):
        row = next(r for r in instances() if r["pid"] == pid)
        host, port = row["gdbEndpoint"].split(":")
        backend = GwemuGDBBackend(host=host, port=int(port))
        backend.open()
        self.dbg = DebugSession(backend, "gwemu", qmp_socket=row["qmpSocket"])
        self.pid = pid
        self.out_dir = out_dir

    def frames(self, buttons: str = "", n: int = 1):
        """Inject a button mask (e.g. "LEFT+GAME"; "" = nothing) for n frames."""
        mask = sum(1 << BITS[b] for b in buttons.split("+") if b)
        for _ in range(n):
            result = self.dbg.inject_return(READ_BUTTONS_RETURN, mask, timeout=10)
            if not result.get("hit"):
                raise RuntimeError(f"read_buttons return site not reached: {result}")

    def tap(self, buttons: str, settle: int = 8):
        self.frames(buttons, 2)
        self.frames("", settle)

    def shot(self, name: str):
        """Screenshot at a frame boundary (target halted)."""
        self.dbg.run_until(READ_BUTTONS_RETURN, timeout=10)
        screenshot_instance(pid=self.pid, output=self.out_dir / f"{name}.png")
        self.dbg.backend.write_register("r0", 0)
        self.dbg.resume()

    def read16(self, addr: int) -> int:
        self.dbg.halt()
        value = struct.unpack("<H", self.dbg.backend.read_memory(addr, 2))[0]
        self.dbg.resume()
        return value

    def pc(self) -> int:
        self.dbg.halt()
        value = self.dbg.backend.read_register("pc")
        self.dbg.resume()
        return value


def check(condition: bool, message: str):
    print(("PASS: " if condition else "FAIL: ") + message)
    return condition


def run(driver: Driver, bank2: str) -> bool:
    # GWemu reports some buttons as held when idle (e.g. TIME on 0.0.23), so
    # every frame's input is injected; never let the firmware run un-driven
    # mid-sequence or the "release" of the stuck button registers as a press.
    driver.frames("", 30)
    driver.tap("TIME", settle=90)  # Leave the first-boot "PRESS TIME" screen.
    driver.shot("01_clock")
    driver.tap("GAME", settle=40)  # Menu slide-in.
    driver.shot("02_menu")

    ok = True
    for i in range(3):
        driver.tap("DOWN")
        driver.shot(f"03_down{i + 1}")
    choice = driver.read16(UI_GAME_SELECT_CHOICE)

    if bank2 == "spin":
        ok &= check(
            choice == CHOICE_DUAL_BOOT,
            f"DUAL BOOT selected after 3x DOWN (choice=0x{choice:X})",
        )
        driver.frames(
            "A", 1
        )  # The firmware resets during this frame; there is no next one.
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
    args = parser.parse_args()

    work = args.work_dir.resolve()
    out_dir = work / f"shots_{args.bank2}"
    out_dir.mkdir(parents=True, exist_ok=True)
    profile = work / "mario-profile"
    bank2 = work / f"bank2_{args.bank2}.bin"
    make_bank2(bank2, args.bank2)
    make_profile(profile, bank2)

    start_instance(str(profile), headless=True, gwemu_bin=str(args.gwemu_bin.resolve()))
    pid = next(r["pid"] for r in instances() if r["profile"] == str(profile))
    try:
        time.sleep(3)
        ok = run(Driver(pid, out_dir), args.bank2)
    finally:
        stop_instance(pid=pid)
    print(f"Screenshots: {out_dir}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
