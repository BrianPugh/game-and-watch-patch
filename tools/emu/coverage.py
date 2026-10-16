#!/usr/bin/env python3
"""Record which stock Mario functions run in each mode.

Arms a breakpoint on every stock function entry, boots the patched build from
reset in a headless GWemu, and plays boot, clock, SMB1, BALL, the pause menu
and SMB2 (last, so anything first seen there is SMB2-only among the paths
played). Each breakpoint is cleared on its first hit and attributed to the
phase it fired in.

Function entries are BL/B.W targets in stock internal flash plus the ``func``
entries of tools/re/mario.syms (or ``--funcs``, one hex address per line, e.g.
exported from a disassembler).

"Never ran" is not "dead": error handlers, nvram writes, sleep, the Mario song
and the factory test aren't exercised. Check callers before removing code.

Notes on GWemu:
* Breakpoints only work when set while halted; resuming from an armed
  breakpoint re-hits it, so clear/step/re-arm.

Usage:
    python tools/emu/coverage.py --gwemu-bin /path/to/gwemu
"""

import argparse
import json
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
import mario_menu_test as menu  # noqa: E402
from emu_driver import Driver, make_bank2, make_profile, mask  # noqa: E402

REPO = Path(__file__).resolve().parents[2]

INTFLASH = 0x0800_0000
STOCK_CODE = (0x0800_02CC, 0x0801_8100)  # After the vector table; see patches/mario.py


def branch_targets(data: bytes):
    """BL and B.W (T4) targets inside STOCK_CODE."""
    lo, hi = STOCK_CODE
    for offset in range(lo - INTFLASH, hi - INTFLASH - 4, 2):
        h1, h2 = struct.unpack_from("<HH", data, offset)
        if (h1 & 0xF800) != 0xF000 or (h2 & 0xD000) not in (0x9000, 0xD000):
            continue
        s = (h1 >> 10) & 1
        j1, j2 = (h2 >> 13) & 1, (h2 >> 11) & 1
        imm = (
            (s << 24)
            | ((1 ^ (j1 ^ s)) << 23)
            | ((1 ^ (j2 ^ s)) << 22)
            | ((h1 & 0x3FF) << 12)
            | ((h2 & 0x7FF) << 1)
        )
        if s:
            imm -= 1 << 25
        target = INTFLASH + offset + 4 + imm
        if lo <= target < hi:
            yield target


def syms_functions(path: Path):
    for line in path.read_text().splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[0].startswith("0x") and parts[1] == "func":
            yield int(parts[0], 16)


def stock_functions(args) -> list[int]:
    if args.funcs:
        funcs = {int(x, 16) for x in args.funcs.read_text().split()}
    else:
        data = (REPO / "internal_flash_backup_mario.bin").read_bytes()
        funcs = set(branch_targets(data)) | set(
            syms_functions(REPO / "tools/re/mario.syms")
        )
    lo, hi = STOCK_CODE
    return sorted(a & ~1 for a in funcs if lo <= a < hi or a < 0x1_0000)


class Tracer:
    def __init__(self, driver: Driver, funcs: list[int]):
        self.dbg, self.backend = driver.dbg, driver.dbg.backend
        self.armed = set()
        self.hits: dict[str, list[int]] = {}
        self.phase = "boot"
        self.dbg.halt()
        for address in funcs:
            if self.dbg.bp(address) == "OK":
                self.armed.add(address)
        self.dbg.bp(menu.READ_BUTTONS_RETURN)
        self.backend.reset()  # Leaves the target running.

    def frame(self, mask: int = 0, at_frame=None):
        """Run to the next read_buttons return and inject ``mask``.

        The target is running on entry and on return.
        """
        while True:
            result = self.dbg.wait_stopped(30)
            if not result.get("stopped"):
                raise RuntimeError("no breakpoint within 30 s")
            pc = result["registers"]["pc"]
            if pc == menu.READ_BUTTONS_RETURN:
                self.backend.write_register("r0", mask)
                if at_frame:
                    at_frame()
                self.dbg.clear_bp(pc)
                self.dbg.step()
                self.dbg.bp(pc)
                self.dbg.resume()
                return
            if pc not in self.armed:
                raise RuntimeError(f"unexpected stop at 0x{pc:08X}")
            self.hits.setdefault(self.phase, []).append(pc)
            self.armed.discard(pc)
            self.dbg.clear_bp(pc)
            self.dbg.resume()

    def frames(self, buttons: str = "", n: int = 1):
        value = mask(buttons)
        for _ in range(n):
            self.frame(value)

    def tap(self, buttons: str, settle: int = 8):
        self.frames(buttons, 2)
        self.frames("", settle)


def play(t: Tracer, downs: int):
    # The menu cursor starts on the current game; order is SMB1, SMB2, BALL.
    t.tap("GAME", settle=40)
    for _ in range(downs):
        t.tap("DOWN")
    t.tap("A", settle=120)
    for _ in range(10):
        t.tap("A", settle=30)
    for _ in range(30):
        t.frames("RIGHT", 20)
        t.frames("RIGHT+A", 10)


PHASES = [
    ("boot", lambda t: (t.frames("", 30), t.tap("TIME", settle=90))),
    ("clock", lambda t: t.frames("", 600)),
    ("smb1", lambda t: play(t, 0)),
    ("ball", lambda t: play(t, 2)),
    (
        "pause",
        lambda t: (
            t.tap("PAUSE", settle=40),
            t.tap("DOWN"),
            t.tap("RIGHT"),
            t.tap("UP"),
            t.tap("LEFT"),
            t.tap("B", settle=40),
        ),
    ),
    ("clock_again", lambda t: (t.tap("TIME", settle=60), t.frames("", 300))),
    ("smb2", lambda t: play(t, 2)),
]


def report(funcs: list[int], hits: dict[str, list[int]], top: int):
    flash = [a for a in funcs if a >= INTFLASH] + [STOCK_CODE[1]]
    size = {flash[i]: flash[i + 1] - flash[i] for i in range(len(flash) - 1)}
    seen = {a for v in hits.values() for a in v}
    print(
        f"\n{'phase':12s} {'funcs':>6s} {'bytes':>7s}  (sizes are distance to the next entry)"
    )
    for phase, addresses in hits.items():
        print(
            f"{phase:12s} {len(addresses):6d} {sum(size.get(a, 0) for a in addresses):7d}"
        )
    never = [a for a in flash[:-1] if a not in seen]
    print(f"{'never ran':12s} {len(never):6d} {sum(size[a] for a in never):7d}")

    runs = []
    for a in never:
        if runs and runs[-1][1] == a:
            runs[-1][1] += size[a]
        else:
            runs.append([a, a + size[a]])
    runs.sort(key=lambda r: r[0] - r[1])
    print("\nLargest never-run ranges:")
    for start, end in runs[:top]:
        print(f"  0x{start:08X}-0x{end:08X} {end - start:6d} B")


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--gwemu-bin", required=True, type=Path, help="Path to the GWemu executable."
    )
    parser.add_argument(
        "--funcs", type=Path, help="Function entries, one hex address per line."
    )
    parser.add_argument("--work-dir", type=Path, default=REPO / "build/emu")
    parser.add_argument("--top", type=int, default=20, help="Never-run ranges to list.")
    args = parser.parse_args()

    work = args.work_dir.resolve()
    out_dir = work / "coverage"
    out_dir.mkdir(parents=True, exist_ok=True)
    profile = work / "mario-profile"
    bank2 = work / "bank2_erased.bin"
    make_bank2(bank2, "erased")
    make_profile(
        profile,
        bank2,
        (REPO / "build/internal_flash_patched.bin").resolve(),
        (REPO / "build/external_flash_patched.bin").resolve(),
    )
    funcs = stock_functions(args)

    start_instance(str(profile), headless=True, gwemu_bin=str(args.gwemu_bin.resolve()))
    pid = next(r["pid"] for r in instances() if r["profile"] == str(profile))
    try:
        time.sleep(3)
        tracer = Tracer(Driver(pid, out_dir, menu.READ_BUTTONS_RETURN), funcs)
        print(f"armed {len(tracer.armed)} functions", flush=True)
        for name, step in PHASES:
            tracer.phase = name
            step(tracer)
            info = {}

            def snapshot(name=name):
                screenshot_instance(pid=pid, output=out_dir / f"{name}.png")
                info["mode"] = struct.unpack(
                    "<H", tracer.backend.read_memory(menu.GNW_MODE, 2)
                )[0]

            tracer.frame(0, snapshot)
            new = len(tracer.hits.get(name, []))
            print(
                f"{name:12s} {new:4d} new functions (gnw_mode=0x{info['mode']:X})",
                flush=True,
            )
    finally:
        stop_instance(pid=pid)

    hits = {k: sorted(v) for k, v in tracer.hits.items()}
    (out_dir / "hits.json").write_text(json.dumps({"funcs": funcs, "hits": hits}))
    report(funcs, hits, args.top)
    print(f"\nScreenshots and hits.json: {out_dir}")


if __name__ == "__main__":
    main()
