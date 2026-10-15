# Emulator testing (GWemu)

Run the patched Mario firmware in [GWemu](https://github.com/slash-proc/gwemu)
(a QEMU fork for the G&W) via [gwprov](https://github.com/slash-proc/gwprov),
drive it with frame-accurate button input, and take screenshots — no hardware
needed.

## Setup

gwprov isn't on PyPI and needs a newer Python than some of this repo's
tooling, so keep it in its own venv:

```bash
python3 -m venv build/gwprov-venv
build/gwprov-venv/bin/pip install 'gwprov[dist,media] @ git+https://github.com/slash-proc/gwprov'
```

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

## How input works (and why not the keyboard)

With stock Mario on GWemu 0.0.23:

* Headless mode ignores QMP key events.
* Even in a window, the firmware reads TIME as permanently held.

So the harness injects buttons through GDB instead: every frame it stops at
the instruction after `bl read_buttons` in `main_loop_tick` (`0x08006B56`,
see `tools/re/mario.syms`) and overwrites `r0` with the desired button mask.

Run a whole sequence within one session. Once injection stops, the firmware
sees the emulator's stuck TIME again, which registers as a fresh TIME press
(closing menus / switching to the clock).
