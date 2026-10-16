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
STATE_TRANSITION = 0x2000103D  # u8; 0 = idle
UI_STATE = 0x20010694  # u8; 0 = no overlay, 5 = overlay open
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


def make_profile(profile: Path, bank2: Path, internal: Path, external: Path):
    """Stock Mario profile with our patched images swapped in."""
    gwprov("backups", "add", REPO)
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
        internal,
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
        external,
        "--offset",
        0,
    )
    gwprov(
        "profile", profile, "edit", "add", "bank2", "Test bank2", bank2, "--offset", 0
    )


def make_bank2(path: Path, kind: str, base: Path = None):
    """Bank 2: ``base`` (or erased flash), with a tiny image or erased header at 0."""
    data = bytearray(b"\xff" * 0x40000)
    if base is not None:
        image = base.read_bytes()
        data[: len(image)] = image
    data[:16] = b"\xff" * 16
    if kind == "spin":
        # Initial SP, reset vector -> 0x08100008 (Thumb), then `b .`.
        data[0:8] = struct.pack("<II", 0x2002_0000, BANK2_SPIN_PC | 1)
        data[8:10] = b"\xfe\xe7"
    path.write_bytes(data)


class Driver:
    """Drive the firmware one input poll at a time.

    A breakpoint stays on ``READ_BUTTONS_RETURN`` for the whole session, and
    between calls the target sits halted there with that frame's input not yet
    injected. So every poll gets an injected value (GWemu's stuck TIME never
    reaches the firmware), and reads and screenshots see a frame boundary.
    """

    def __init__(self, pid: int, out_dir: Path):
        row = next(r for r in instances() if r["pid"] == pid)
        host, port = row["gdbEndpoint"].split(":")
        backend = GwemuGDBBackend(host=host, port=int(port))
        backend.open()
        self.dbg = DebugSession(backend, "gwemu", qmp_socket=row["qmpSocket"])
        self.pid = pid
        self.out_dir = out_dir
        self.at_poll = False

    def _wait_poll(self):
        result = self.dbg.wait_stopped(10)
        if not (
            result.get("stopped") and result["registers"]["pc"] == READ_BUTTONS_RETURN
        ):
            raise RuntimeError(f"read_buttons return site not reached: {result}")
        self.at_poll = True

    def run_to_poll(self):
        """Resume a halted target and stop at its next poll."""
        self.dbg.bp(READ_BUTTONS_RETURN)
        self.dbg.resume()
        self._wait_poll()

    def _sync(self):
        """Halt at the next poll (on first use, or after the target ran free)."""
        if not self.at_poll:
            self.dbg.halt()
            self.run_to_poll()

    def _release(self, mask: int):
        """Inject ``mask`` into the current poll and step off the breakpoint."""
        self._sync()
        self.dbg.backend.write_register("r0", mask)
        self.dbg.clear_bp(READ_BUTTONS_RETURN)
        self.dbg.step()
        self.at_poll = False

    def frames(self, buttons: str = "", n: int = 1):
        """Inject a button mask (e.g. "LEFT+GAME"; "" = nothing) for n frames."""
        mask = sum(1 << BITS[b] for b in buttons.split("+") if b)
        for _ in range(n):
            self._release(mask)
            self.run_to_poll()

    def last_frame(self, buttons: str):
        """Inject one frame and let the target run free (e.g. it resets)."""
        self._release(sum(1 << BITS[b] for b in buttons.split("+") if b))
        self.dbg.resume()

    def tap(self, buttons: str, settle: int = 8):
        self.frames(buttons, 2)
        self.frames("", settle)

    def shot(self, name: str):
        """Screenshot at a frame boundary."""
        self._sync()
        screenshot_instance(pid=self.pid, output=self.out_dir / f"{name}.png")

    def read8(self, addr: int) -> int:
        self._sync()
        return self.dbg.backend.read_memory(addr, 1)[0]

    def read16(self, addr: int) -> int:
        self._sync()
        return struct.unpack("<H", self.dbg.backend.read_memory(addr, 2))[0]

    def wait_until(self, condition, what: str, max_frames: int = 600, step: int = 10):
        """Run idle frames until ``condition()``; animations don't take a
        fixed number of frames (relocated builds needed different counts)."""
        for _ in range(0, max_frames, step):
            if condition():
                return
            self.frames("", step)
        raise RuntimeError(f"timed out waiting for {what}")

    def press_until(self, buttons: str, condition, what: str, tries: int = 10):
        """Tap ``buttons`` until ``condition()``; the firmware ignores some
        presses (e.g. GAME during a clock animation)."""
        for _ in range(tries):
            self.tap(buttons, settle=10)
            try:
                return self.wait_until(condition, what, max_frames=120)
            except RuntimeError:
                pass
        raise RuntimeError(f"{buttons} never led to {what}")

    def pc(self) -> int:
        """PC wherever the target is (it may never reach another poll)."""
        if not self.at_poll:
            self.dbg.halt()
        return self.dbg.backend.read_register("pc")


def check(condition: bool, message: str):
    print(("PASS: " if condition else "FAIL: ") + message)
    return condition


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
    global READ_BUTTONS_RETURN
    READ_BUTTONS_RETURN += args.stock_offset

    work = args.work_dir.resolve()
    out_dir = work / f"shots_{args.bank2}"
    out_dir.mkdir(parents=True, exist_ok=True)
    profile = work / "mario-profile"
    bank2 = work / f"bank2_{args.bank2}.bin"
    make_bank2(bank2, args.bank2, args.bank2_image)
    make_profile(profile, bank2, args.internal.resolve(), args.external.resolve())

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
