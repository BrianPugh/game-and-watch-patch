# Emulator testing (GWemu)

Run the patched Mario or Zelda firmware in [GWemu](https://github.com/slash-proc/gwemu)
(a QEMU fork for the G&W) via [gwprov](https://github.com/slash-proc/gwprov),
drive it with frame-accurate button input, and take screenshots — no hardware
needed.

## Setup

gwprov isn't on PyPI and needs a newer Python than some of this repo's
tooling, so keep it in its own venv:

```bash
python3 -m venv build/gwprov-venv
build/gwprov-venv/bin/pip install 'gwprov[dist,media] @ git+https://github.com/slash-proc/gwprov' numpy
```

numpy lets scripts import `patches` (e.g. `patches.tileset`) from this venv.

Download a GWemu build from the
[releases page](https://github.com/slash-proc/gwemu/releases) (e.g.
`gwemu-macos-arm64-app.zip`). After unzipping, restore the executable bit:
`chmod +x gwemu.app/Contents/MacOS/gwemu`.

## Game-select menu test

```bash
make PATCH_PARAMS="--device=mario --game-select-dual-boot"
build/gwprov-venv/bin/python tools/emu/mario_menu_test.py --gwemu-bin /path/to/gwemu
build/gwprov-venv/bin/python tools/emu/mario_menu_test.py --gwemu-bin /path/to/gwemu --bank2 erased
```

Screenshots go to `build/emu/shots_<bank2>/`. The script exits non-zero if a
check fails.

## Zelda test

```bash
make PATCH_PARAMS="--device=zelda"
build/gwprov-venv/bin/python tools/emu/zelda_menu_test.py --gwemu-bin /path/to/gwemu [--only la_en,timer]
```

Leaves the first-boot PRESS TIME screen, then launches every game-select
variant (LoZ1/LoZ2 EN/JP, LA EN/FR/DE/JP, Vermin, Timer) from the clock,
checks `g_app_id` and that the frame isn't blank, and returns with TIME.
Screenshots go to `build/emu/zelda_shots/`. A full run takes ~5 minutes.

For a `--game-select-dual-boot` build, add `--dual-boot spin` (a tiny valid
image in bank 2: checks the 6th row, its scrolling window and that A runs
bank 2) or `--dual-boot erased` (the row must be hidden).

For a `--no-la` build, add `--no-la`: it skips the LA games, expects Vermin
and Timer a row up and the cursor to wrap over 4 rows (5 with DUAL BOOT, which
then takes LA's old slot without scrolling).

gwprov registers one backup pair per directory, so it only finds Mario when
both pairs sit in the repo root. `emu_driver.make_profile` registers each
model from its own directory of symlinks (`build/emu/<model>-backups/`).

Shared pieces (`Driver`, profiles, button bits) live in `emu_driver.py`.

## How input works (and why not the keyboard)

With stock Mario on GWemu 0.0.23:

* Headless mode ignores QMP key events.
* Even in a window, the firmware reads TIME as permanently held.

So the harness injects buttons through GDB instead: every frame it stops at
the instruction after `bl read_buttons` in `main_loop_tick` (Mario
`0x08006B56`, Zelda `0x0800FE58`; see `tools/re/<device>.syms`) and
overwrites `r0` with the desired button mask.

Run a whole sequence within one session. Once injection stops, the firmware
sees the emulator's stuck TIME again, which registers as a fresh TIME press
(closing menus / switching to the clock).

## RAM probe

```bash
make PATCH_PARAMS="--device=mario"
build/gwprov-venv/bin/python tools/emu/ram_probe.py --gwemu-bin /path/to/gwemu [--device zelda]
```

Fills RAM with a marker at the stock `Reset_Handler`, plays every mode
(Mario: clock, SMB1, SMB2, BALL and the pause menu; Zelda: clock and every
game), then lists regions that still hold the marker
(never written by the stock firmware). Our own `.data`/`.bss`/stack is skipped.
GWemu fills some buffers (e.g. audio) in some runs only, so run it more than
once and trust only regions that are free every time. `--stock` boots the
unpatched backups and paints all of RAM, which shows what stock uses inside
our linker region (on Zelda that region is stock's per-game state, see
`tools/re/zelda.syms`). Results are recorded in `tools/re/<device>.syms`.

## Function coverage

```bash
build/gwprov-venv/bin/python tools/emu/coverage.py --gwemu-bin /path/to/gwemu
```

Breaks on every stock function entry from reset and records the first mode
(boot, clock, SMB1, BALL, pause, SMB2) each one runs in, then lists the
largest never-run ranges. That's how the factory test (`--no-factory-test`)
was found. "Never ran" isn't proof of dead code: check callers first, and
re-run this after removing code (removed ranges are filled with `udf`, so a
missed path faults).

GWemu only honors breakpoints set while the target is halted, and resuming
from an armed breakpoint re-hits it; the tool clears, steps and re-arms.

## Relocated stock image

```bash
python tools/re/shift_image.py --device=mario
build/gwprov-venv/bin/python tools/emu/shift_test.py --gwemu-bin /path/to/gwemu
```

Boots the stock image relocated to bank 2 (see `tools/re/README.md`) and
plays the `coverage.py` phases with input injected at the shifted
`read_buttons` return. A phase fails if that site stops being reached (fault
or hang) or the CPU is caught in bank 1. `--images build/re/shift/stock --delta 0`
runs the same steps on unshifted images to check the harness itself.

## Relocated patched build

```bash
make PATCH_PARAMS="--device=mario --slim --game-select-dual-boot --relocate-stock=0x08120000"
build/gwprov-venv/bin/python tools/emu/mario_menu_test.py --gwemu-bin /path/to/gwemu \
    --bank2-image build/internal_flash_patched_bank2.bin --stock-offset 0x120000
build/gwprov-venv/bin/python tools/emu/shift_test.py --gwemu-bin /path/to/gwemu --delta 0x120000 \
    --bank1 build/internal_flash_patched.bin --bank2 build/internal_flash_patched_bank2.bin \
    --external build/external_flash_patched.bin
```

`--relocate-stock` moves all stock code (everything below the init table at
`0x0801807c`) to bank 2 and leaves `udf` behind; our code stays in bank 1. The
menu test's dual-boot stub stays at `0x08100000`, which is why the firmware
goes to `0x08120000`. Both tools wait on firmware state rather than counting
frames, and retry presses the stock UI ignores (e.g. during clock animations).

Zelda works the same way (stock code below its init table at `0x0801b368`):

```bash
make PATCH_PARAMS="--device=zelda --relocate-stock=0x08120000"
build/gwprov-venv/bin/python tools/emu/zelda_menu_test.py --gwemu-bin /path/to/gwemu \
    --bank2-image build/internal_flash_patched_bank2.bin --stock-offset 0x120000
```

The destination can also be in bank 1, past our code and data (the layout the
Mario+Zelda amalgam uses for Zelda): the image is then 256 KB with the stock
code at `0x08020000`, and bank 2 stays free.

```bash
make PATCH_PARAMS="--device=zelda --extended --relocate-stock=0x08020000"
build/gwprov-venv/bin/python tools/emu/zelda_menu_test.py --gwemu-bin /path/to/gwemu \
    --stock-offset 0x20000
make PATCH_PARAMS="--device=mario --extended --game-select-dual-boot --relocate-stock=0x08020000"
build/gwprov-venv/bin/python tools/emu/mario_menu_test.py --gwemu-bin /path/to/gwemu \
    --stock-offset 0x20000
build/gwprov-venv/bin/python tools/emu/shift_test.py --gwemu-bin /path/to/gwemu --delta 0x20000 \
    --bank1 build/internal_flash_patched.bin --bank2 <an erased bank-2 file> \
    --external build/external_flash_patched.bin
```

## Amalgam (Mario + Zelda in one image)

`--device=amalgam` boots Mario until a game-select DUAL BOOT entry (always on in
the amalgam, labeled ZELDA/MARIO) switches firmware with a reset. Mario's stock
code stays in place (no `--delta`), and its external data starts after Zelda's:

```bash
make PATCH_PARAMS="--device=amalgam"
build/gwprov-venv/bin/python tools/emu/zelda_menu_test.py --gwemu-bin /path/to/gwemu --amalgam
build/gwprov-venv/bin/python tools/emu/shift_test.py --gwemu-bin /path/to/gwemu --delta 0 \
    --bank1 build/internal_flash_patched.bin --bank2 <an erased bank-2 file> \
    --external build/external_flash_patched.bin
```

`zelda_menu_test.py --amalgam` boots Mario, picks its ZELDA entry, runs the
Zelda checks on the firmware it resets into (Zelda's code below its factory test at `+0x1B400`), and
ends with Zelda's MARIO row resetting back into Mario. The selection lives in
TAMP backup register 31, which GWemu clears on reset; the harness checks the
value written before the reset and writes it back when the bootloader starts.
Persistence across power-off needs hardware. Mario comes back at its PRESS
TIME screen in GWemu (check on hardware whether it keeps the time). `shift_test.py` covers Mario only.
