#!/usr/bin/env python3
"""Find RAM the stock Mario firmware never writes.

Boots the patched build in a headless GWemu, halts at the stock
``Reset_Handler`` (after our bootloader, before stock init), fills RAM with a
marker pattern, then plays clock, SMB1, SMB2, BALL and the pause menu with
injected buttons. Afterwards any 16-byte block still holding the marker was
never written.

Our own RAM (``build/gw_patch.elf`` .data/.bss/heap/stack) is left unpainted;
the unused rest of our linker RAM region is painted and reported.

Caveats:
* Coverage is limited to the paths played here. Sleep images, the Mario song
  and nvram writes are not exercised.
* Some buffers (e.g. audio DMA) are filled by GWemu in some runs only. Run it
  more than once and trust only regions that are free in every run.

Requires gwprov and a GWemu binary; see tools/emu/README.md.

Usage:
    python tools/emu/ram_probe.py --gwemu-bin /path/to/gwemu
"""

import argparse
import sys
import time
from pathlib import Path

from elftools.elf.elffile import ELFFile
from gwprov.gwemu_manager import instances, start_instance, stop_instance

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mario_menu_test as menu  # noqa: E402

REPO = Path(__file__).resolve().parents[2]

STOCK_RESET_HANDLER = 0x08017A44  # See tools/re/mario.syms.
MARKER = bytes.fromhex("a55ac33c")

# (name, start, end) of every RAM bank on the STM32H7B0.
RAM_BANKS = [
    ("ITCM", 0x0000_0000, 0x0001_0000),
    ("DTCM", 0x2000_0000, 0x2002_0000),
    ("AXI", 0x2400_0000, 0x2410_0000),
    ("AHB", 0x3000_0000, 0x3002_0000),
    ("SRD", 0x3800_0000, 0x3800_8000),
]


def our_ram(elf_path: Path) -> tuple[int, int]:
    """[start, end) of RAM used by our .data/.bss/heap/stack."""
    with elf_path.open("rb") as f:
        sections = [
            s
            for s in ELFFile(f).iter_sections()
            if s.name in (".data", ".bss", "._user_heap_stack")
        ]
        start = min(s["sh_addr"] for s in sections)
        end = max(s["sh_addr"] + s["sh_size"] for s in sections)
    return start, (end + 0xF) & ~0xF


def paint_ranges(sp: int, skip: tuple[int, int]):
    """RAM_BANKS minus our RAM and the live stack."""
    for name, start, end in RAM_BANKS:
        if start <= sp < end:
            end = (sp - 0x100) & ~0xF
        if start <= skip[0] < end:
            yield name, start, skip[0]
            yield name, skip[1], end
        else:
            yield name, start, end


def untouched_runs(data: bytes, base: int):
    block = MARKER * 4
    start = None
    for offset in range(0, len(data), 16):
        if data[offset : offset + 16] == block:
            if start is None:
                start = offset
        elif start is not None:
            yield base + start, base + offset
            start = None
    if start is not None:
        yield base + start, base + len(data)


def exercise(driver: menu.Driver, idle_frames: int):
    def play(downs: int):
        # The menu cursor starts on the current game.
        driver.tap("GAME", settle=40)
        for _ in range(downs):
            driver.tap("DOWN")
        driver.tap("A", settle=120)
        for _ in range(10):
            driver.tap("A", settle=30)
        for _ in range(30):
            driver.frames("RIGHT", 20)
            driver.frames("RIGHT+A", 10)

    steps = [
        ("boot", lambda: (driver.frames("", 30), driver.tap("TIME", settle=90))),
        ("clock", lambda: driver.frames("", 600)),
        ("smb1", lambda: play(0)),
        ("smb2", lambda: play(1)),
        ("ball", lambda: play(1)),
        (
            "pause",
            lambda: (
                driver.tap("PAUSE", settle=40),
                driver.tap("DOWN"),
                driver.tap("RIGHT"),
                driver.tap("B", settle=40),
            ),
        ),
        (
            "clock_again",
            lambda: (driver.tap("TIME", settle=60), driver.frames("", 300)),
        ),
    ]
    if idle_frames:
        steps.append(("idle", lambda: driver.frames("", idle_frames)))
    for i, (name, step) in enumerate(steps):
        step()
        driver.shot(f"{i:02d}_{name}")
        print(f"{name} done", flush=True)


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--gwemu-bin", required=True, type=Path, help="Path to the GWemu executable."
    )
    parser.add_argument("--work-dir", type=Path, default=REPO / "build/emu")
    parser.add_argument(
        "--min-run", type=int, default=512, help="Smallest region to report (bytes)."
    )
    parser.add_argument(
        "--idle-frames",
        type=int,
        default=0,
        help="Extra clock frames at the end (e.g. to reach the sleep screen).",
    )
    args = parser.parse_args()

    work = args.work_dir.resolve()
    out_dir = work / "ram_probe"
    out_dir.mkdir(parents=True, exist_ok=True)
    profile = work / "mario-profile"
    bank2 = work / "bank2_erased.bin"
    menu.make_bank2(bank2, "erased")
    menu.make_profile(
        profile,
        bank2,
        (REPO / "build/internal_flash_patched.bin").resolve(),
        (REPO / "build/external_flash_patched.bin").resolve(),
    )
    skip = our_ram(REPO / "build/gw_patch.elf")

    start_instance(str(profile), headless=True, gwemu_bin=str(args.gwemu_bin.resolve()))
    pid = next(r["pid"] for r in instances() if r["profile"] == str(profile))
    try:
        time.sleep(3)
        driver = menu.Driver(pid, out_dir)
        dbg, backend = driver.dbg, driver.dbg.backend

        dbg.halt()
        dbg.bp(STOCK_RESET_HANDLER)
        backend.reset()
        regs = dbg.wait_stopped(10)["registers"]
        dbg.clear_bp(STOCK_RESET_HANDLER)
        if regs["pc"] != STOCK_RESET_HANDLER:
            raise RuntimeError(
                f"expected stock Reset_Handler, stopped at 0x{regs['pc']:08X}"
            )

        ranges = list(paint_ranges(regs["sp"], skip))
        for _, start, end in ranges:
            backend.write_memory(start, MARKER * ((end - start) // len(MARKER)))
        driver.run_to_poll()

        exercise(driver, args.idle_frames)

        dbg.halt()
        print(
            f"\nNever written (>= {args.min_run} B); our RAM 0x{skip[0]:08X}-0x{skip[1]:08X} skipped:"
        )
        for name, start, end in ranges:
            data = backend.read_memory(start, end - start)
            for a, b in untouched_runs(data, start):
                if b - a >= args.min_run:
                    print(f"  {name:5s} 0x{a:08X}-0x{b:08X} {b - a:7d} B")
    finally:
        stop_instance(pid=pid)
    print(f"Screenshots: {out_dir}")


if __name__ == "__main__":
    main()
