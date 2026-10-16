#!/usr/bin/env python3
"""Find RAM the stock firmware never writes.

Boots the patched build in a headless GWemu, halts at the stock
``Reset_Handler`` (after our bootloader, before stock init), fills RAM with a
marker pattern, then plays every mode with injected buttons (Mario: clock,
SMB1, SMB2, BALL and the pause menu; Zelda: clock and every game-select
variant). Afterwards any 16-byte block still holding the marker was
never written.

Our own RAM (``build/gw_patch.elf`` .data/.bss/heap/stack) is left unpainted;
the unused rest of our linker RAM region is painted and reported. ``--stock``
boots the unpatched backups instead and paints all of RAM, which shows what
stock itself uses inside our linker region.

Caveats:
* Coverage is limited to the paths played here. Sleep images, the Mario song
  and nvram writes are not exercised, and each game is only played briefly.
* Some buffers (e.g. audio DMA) are filled by GWemu in some runs only. Run it
  more than once and trust only regions that are free in every run.

Requires gwprov and a GWemu binary; see tools/emu/README.md.

Usage:
    python tools/emu/ram_probe.py --gwemu-bin /path/to/gwemu [--device zelda] [--stock]
"""

import argparse
import sys
import time
from pathlib import Path
from typing import Optional

from elftools.elf.elffile import ELFFile
from gwprov.gwemu_manager import instances, start_instance, stop_instance

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mario_menu_test  # noqa: E402
import zelda_menu_test  # noqa: E402
from emu_driver import Driver, make_bank2, make_profile  # noqa: E402

REPO = Path(__file__).resolve().parents[2]

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


def paint_ranges(sp: int, skip: Optional[tuple[int, int]]):
    """RAM_BANKS minus our RAM (if any) and the live stack."""
    for name, start, end in RAM_BANKS:
        if start <= sp < end:
            end = (sp - 0x100) & ~0xF
        if skip is not None and start <= skip[0] < end:
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


def exercise_mario(driver: Driver):
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
    return steps


def exercise_zelda(driver: Driver):
    zelda = zelda_menu_test

    def play(name: str, row: int, variant: int, app_id: int):
        zelda.launch(driver, row, variant, app_id)
        driver.frames("", 120)
        for _ in range(6):
            driver.tap("START", settle=60)
        for _ in range(20):
            driver.frames("RIGHT", 20)
            driver.frames("UP+A", 10)
            driver.frames("LEFT+B", 10)
        driver.shot(f"{name}_playing")
        zelda.to_clock(driver)

    steps = [
        ("boot", lambda: (driver.frames("", 30), zelda.to_clock(driver))),
        ("clock", lambda: driver.frames("", 600)),
    ]
    for name, row, variant, app_id in zelda.GAMES:
        steps.append((name, lambda g=(name, row, variant, app_id): play(*g)))
    return steps


# Stock Reset_Handler (tools/re/<device>.syms), read_buttons return site,
# and the play-through for each device.
DEVICES = {
    "mario": (0x08017A44, mario_menu_test.READ_BUTTONS_RETURN, exercise_mario),
    "zelda": (0x0801AD48, zelda_menu_test.READ_BUTTONS_RETURN, exercise_zelda),
}


def exercise(driver: Driver, device: str, idle_frames: int):
    steps = DEVICES[device][2](driver)
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
    parser.add_argument("--device", choices=DEVICES, default="mario")
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
    parser.add_argument(
        "--stock",
        action="store_true",
        help="Boot the stock backups and paint all of RAM (no patched build needed).",
    )
    args = parser.parse_args()

    work = args.work_dir.resolve()
    out_dir = work / f"ram_probe_{args.device}{'_stock' if args.stock else ''}"
    out_dir.mkdir(parents=True, exist_ok=True)
    profile = work / f"{args.device}-profile"
    reset_handler, poll_pc, _ = DEVICES[args.device]
    bank2 = work / "bank2_erased.bin"
    make_bank2(bank2, "erased")
    if args.stock:
        internal = REPO / f"internal_flash_backup_{args.device}.bin"
        external = REPO / f"flash_backup_{args.device}.bin"
        skip = None
    else:
        internal = REPO / "build/internal_flash_patched.bin"
        external = REPO / "build/external_flash_patched.bin"
        skip = our_ram(REPO / "build/gw_patch.elf")
    make_profile(
        profile, bank2, internal.resolve(), external.resolve(), model=args.device
    )

    start_instance(str(profile), headless=True, gwemu_bin=str(args.gwemu_bin.resolve()))
    pid = next(r["pid"] for r in instances() if r["profile"] == str(profile))
    try:
        time.sleep(3)
        driver = Driver(pid, out_dir, poll_pc)
        dbg, backend = driver.dbg, driver.dbg.backend

        dbg.halt()
        dbg.bp(reset_handler)
        backend.reset()
        regs = dbg.wait_stopped(10)["registers"]
        dbg.clear_bp(reset_handler)
        if regs["pc"] != reset_handler:
            raise RuntimeError(
                f"expected stock Reset_Handler, stopped at 0x{regs['pc']:08X}"
            )

        ranges = list(paint_ranges(regs["sp"], skip))
        for _, start, end in ranges:
            backend.write_memory(start, MARKER * ((end - start) // len(MARKER)))
        driver.run_to_poll()

        exercise(driver, args.device, args.idle_frames)

        dbg.halt()
        skipped = (
            f"our RAM 0x{skip[0]:08X}-0x{skip[1]:08X} skipped"
            if skip
            else "stock images"
        )
        print(f"\nNever written (>= {args.min_run} B); {skipped}:")
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
