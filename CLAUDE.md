# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Custom firmware patcher for Nintendo Game & Watch consoles (Mario and Zelda models). Allows running custom code alongside stock firmware, enabling integration with retro-go emulator, ROM hacks, graphics mods, and LZMA-compressed data storage.

## Build Commands

```bash
# Build and flash (most common)
make PATCH_PARAMS="--device=mario" flash
make PATCH_PARAMS="--device=zelda" flash

# Internal-only mode for Mario (no external flash needed)
make PATCH_PARAMS="--device=mario --internal-only" flash

# Clean build
make clean

# Build without flashing
make PATCH_PARAMS="--device=mario"

# See all configuration options
make help
```

Key environment variables:
- `PATCH_PARAMS` - Patching options passed to `patch.py`
- `GNWMANAGER_PATH` - Override gnwmanager binary location (default: `gnwmanager`)
- `FORCE_PYOCD` - Set to `1` to force pyocd backend for gnwmanager
- `GNWMANAGER_DEBUG` - Set to `1` for verbose gnwmanager output
- `GCC_PATH` - Override arm-none-eabi-gcc toolchain path
- `PYTHON` - Override Python binary (default: `python3`)

Notable `PATCH_PARAMS` flags: `--device`, `--extended`, `--internal-only`, `--encrypt`, `--triple-boot`, `--sd-bootloader`, `--clock-only`, `--smb1-graphics`, `--debug`, `--show`.

## Linting and Testing

```bash
# Run pre-commit hooks (black, flake8, isort, trailing-whitespace, end-of-file-fixer, debug-statements)
pre-commit run --all-files

# Run tests (use the repo venv; system python lacks colorama/pycryptodome)
.venv/bin/python -m pytest tests/
```

`tests/test_golden.py` builds 9 flag sets (C + patch) in copies of the working tree and compares output SHA1s with `tests/golden.json` (~30 s; each build runs the stock xrefs analysis). The hashes are tied to the gcc version (other versions skip), and the test skips without the stock backups. When output is meant to change, re-record with `UPDATE_GOLDEN=1 .venv/bin/python -m pytest tests/test_golden.py` and commit the new hashes; diffing the old and new image of one config (`cmp -l`) shows the change is where you expect. To show a refactor is output-neutral beyond those 9 configs, build other flag sets (e.g. `--no-smb2`, `--extended`, `--compression-ratio=0`/`1000` for the fallback paths) in 2 `git worktree`s (main and the branch, with the backups symlinked in) and compare image hashes.

Build gotchas:
- `make clean` deletes all of `build/`, including `build/re/` and `build/emu/`. To force a C rebuild, delete only `build/*.o build/*.d build/gw_patch.*`.
- Run `make` with `PYTHON=.venv/bin/python`. With system python, device detection fails on a missing import and the C build dies with a misleading `#error "Invalid GNW Device specified."`.
- Relocated internal data starts at the linker symbol `__flash_end__` (end of our code/data).
- The patch step tracks `patches/` and `tools/re/*.syms`, but macOS make 3.81 compares mtimes at 1 s resolution: an edit within a second of the last build can be missed.
- Don't use `make -B`: forcing every prerequisite makes make's built-in rules drop byte-identical `*.1` copies next to the `Drivers/` sources and headers.
- To keep single-model output byte-identical while adding an amalgam-only wrapper, alias it with `#define` in the single-model case: at `-Os` gcc kept a `static inline` wrapper out of line, and `always_inline` still changed the caller's code.
- `flash_backup_<device>.bin` is OTFDEC-encrypted. For offline analysis of external data use `build/decrypt.bin`, which every patch run writes before patching.

Configuration: `.flake8` (max-line-length=120, extend-ignore=E203/W503), `.pre-commit-config.yaml` (excludes `Drivers/` directory). Python dependencies in `requirements.txt`.

## Architecture

### Two-Stage Build Process
1. ARM C code in `Core/` compiled to `build/gw_patch.elf` via arm-none-eabi-gcc
2. Python patching (`patch.py`) merges compiled binary with stock firmware backups

The Makefile auto-detects the device from `PATCH_PARAMS` and generates `build/device.ld` with device-specific linker variables (via `scripts/device_from_patch_params.py`). The main linker script `STM32H7B0VBTx_FLASH.ld` includes this generated file.

### Amalgam Build (Mario + Zelda in One Bank, In Progress)
`--device=amalgam` builds one 256 KB bank-1 image and one external image for both firmwares (unencrypted; `--encrypt`, `--triple-boot`, `--sd-bootloader`, `--relocate-stock` and `--internal-only` are rejected). The bootloader boots Zelda (through its relocated vector table, setting VTOR, since Zelda's SystemInit no longer does) if TAMP backup register 31 holds `"ZELD"`, else Mario; it survives power-off (standby wakes through reset), and holding GAME at boot clears it. Each model's game-select DUAL BOOT entry is always on there and labeled with the other firmware: it writes the register and resets (`game_select_launch`, `main.c`). LEFT+GAME still boots bank 2.

C build: both models' sources with `-DGNW_DEVICE_MARIO=1 -DGNW_DEVICE_ZELDA=1`, linked with `STM32H7B0VBTx_FLASH_amalgam.ld`; our code starts past both stock images (0x0801B3E0). Each firmware owns all RAM while it runs, so `*mario.o` keep their RAM in Mario's window and `*zelda.o` in Zelda's. Code both run (`main.o`, `game_select.o`, `ips.o`, `LzmaDec.o`) can't have RAM (a linker `ASSERT`); bootloader-only HAL/CMSIS RAM sits in Mario's window. Shared code picks the running firmware's symbol with `MODEL_SELECT(mario, zelda)` (`Core/Inc/model.h`: `SCB->VTOR` in the amalgam, compile-time otherwise), and each firmware's `bss_rwdata_init` initializes only its own RAM. Hooks both models define carry a model prefix (`mario_game_select_draw`). `tests/test_amalgam.py` checks the placement (toolchain only, no backups).

Patching (`patches/amalgam.py`): each device is patched in its own buffers at stock addresses, both allocating from one bank-1 `Region` (Mario's code stays at 0x08000000; 0x08018100-0x0801B368 opens only after Zelda is patched, since in Zelda's buffer it's still the stock code its relocation copies out; Zelda's init table stays at 0x0801B368; Zelda's stock code moves to the top of the bank, 0x08024000, vector table included). Zelda goes first, then Mario's external data starts past Zelda's (`Device.set_ext_base`: every Mario byte moves, so `fix_ext_refs` fixes every pointer; its NVRAM offsets switch to `mov.w` + `addne.w` at an encodable address). Each device parses the command line with its own defaults (shared option names apply to both). `merge_internal` takes each allocation from its owner's buffer and our code from whichever device changed it; a write into the other device's range, or both changing a byte differently, fails. A device's freed holes are handed out at their final place (`Device.freed_int_ranges`), so either device's data can go there. `Relocation.apply` only re-targets our calls to its own device's `stock_<device>_*` symbols. Both run in GWemu from the amalgam, switching through the menu (`zelda_menu_test.py --amalgam`, `tools/emu/README.md`); GWemu clears the TAMP registers on reset, so the harness re-writes the selection at the bootloader and persistence needs hardware.

### Key Directories
- `Core/Src/` - Custom firmware C code: `main.c` (bootloader, LZMA, hooks both models share), `mario.c`/`zelda.c` (model-only code), `game_select*.c` (the dual-boot menu entry: shared drawing in `game_select.c`, one file per model), ips.c, system_stm32h7xx.c. The Makefile compiles a model's sources (names ending in the model) only for that model
- `Core/Inc/` - Headers; `stock_firmware.h` dispatches to device-specific headers (`stock_firmware_mario.h`, `stock_firmware_zelda.h`) via preprocessor
- `Core/lzma/` - LZMA decoder (7zip SDK) for on-device asset decompression
- `patches/` - Python patching utilities and device-specific logic
- `scripts/` - Build utilities (env checking, device detection/linker generation)
- `Drivers/` - STM32H7 HAL drivers (vendor SDK, excluded from linting)

### Device Registry Pattern
`patches/firmware.py` defines `Device` base class with `__init_subclass__` registry. Device-specific implementations:
- `patches/mario.py` - Mario G&W (`MarioGnW` class)
- `patches/zelda.py` - Zelda G&W (`ZeldaGnW` class)

Each device class adds its own CLI arguments via `argparse()` method.

### Python Patching System
`patches/patch.py` provides `FirmwarePatchMixin` with patching API:
- `replace()`, `relative()`, `lookup()` - Binary patching primitives
- `asm()` - ARM Thumb assembly via `patches/thumb_asm.py`, a minimal in-house assembler (ported from gnwmanager) covering only the forms the patches use; add a form there (with capstone/keystone tests in `tests/test_thumb_asm.py`) before using a new instruction. keystone-engine is an optional dev-only test oracle.
  `asm()` assembles every branch except `b.w` as if at address 0, so for code with relative branches or pc-relative loads (or ITCM code stored elsewhere), call `thumb_assemble(code, run_address)` and write it with `replace(..., expect=...)`.
- `bl()`, `b()`, `nop()` - ARM branch/nop helpers
- `compress()` - LZMA compression for data relocation

Data relocation priority: compressed memory (SRAM3) > internal flash > external flash. Each target is a `Region` (`patches/layout.py`) on the Device: `int_space` (cursor from `__flash_end__`, holes from `free_int`, room held back for the compressed rwdata/SRAM3 images), `sram3_space`, `ext_space` (`ExtRegion`, in-order compaction) and `bank2_space`. Every allocation records its owner and label. `patch.py` runs the Device through a `Layout` (which owns bank 2, shared between devices) and writes every region's contents to `build/layout.txt` (stock/our code, moved blocks, holes, kept external ranges, NVRAM/save areas).

Other key modules: `compression.py` (LZMA/LZ77), `ips.py` (IPS patch format), `tileset.py` (NES graphics), `utils.py` (helpers).

### Memory Layout
- Internal flash: up to 256KB (128KB standard, 256KB with `--extended`)
- External flash: device-dependent (4MB Zelda stock, upgradeable)
- Our RAM (`.data`/`.bss`, from `build/device.ld`): Mario `0x30010000`, Zelda `0x240d3560`-`0x240d6000`. Most Zelda AXI RAM that looks free is stock's per-app state (`app_state_*` in `zelda.syms`): zeroed at boot after our init and saved on every app switch, so anything we keep there must be boot-only. Find free RAM with `ram_probe.py --stock` (the patched-build probe skips our own region).
- Zelda external flash (`ZeldaGnW._compact_ext`/`_finish_ext`): 0-0x20000 (NVRAM banks, save slots, factory scratch) stays put; the data from 0x20000 is compacted with `ExtRegion.keep`/`drop`/`pack` (`patches/layout.py`, `self.ext_space`; shifts in 4 KB steps, `EXT_SHIFT_ALIGN`, word steps for Mario). Blocks stock memcpy's into RAM (NES ROMs, clock graphics, the ITCM/DTCM overlay) are LZMA'd and loaded with `memcpy_inflate`; the overlay holds pointers, so it's compressed after `fix_ext_refs()` and appended at the end. Then the LA save states (4 x 32 KB, 32 KB aligned for `factory_reset_erase_saves`' block erases) follow it. Stock reads saves by flash offset only below 0x20000 and at the LA save states.
- LZMA uses lc=0: the props in `patches/compression.py` and `LZMA_PROP_DATA` (`main.c`)/`LZMA_BUF_SIZE` (`main.h`) must match. The heap is only the probability model (the output buffer is the dictionary), so lc sets its size.
- The SRAM3 packer fills internal flash to within bytes, and the pointer fixups after packing can grow the compressed blobs; `Device.COMPRESSED_FIXUP_SLACK` covers that. A `NotEnoughSpaceError` a few bytes past 0x20000 at `write_table_and_data` means it was too small.
- Firmware metadata at internal vector table offset `0x01B8`: 4-byte structure encoding external flash size and device type (magic value `0x4` distinguishes from executable code)

### Bootloader
- Multi-boot system with magic values in backup registers
- `BOOTLOADER_MAGIC_FORCE` (`0x45435246`) for internal forced boots
- Sanity checks on PC/SP before launching other firmware (prevents crashes from corrupt data)
- Press GAME button on boot to abort dual-boot
- `--triple-boot`: RIGHT+GAME launches firmware at `0x08020000`
- `--sd-bootloader`: Changes dual-boot target to `0x08032000`

### Reverse Engineering & Emulator Testing
- `tools/re/build_elf.py --device=mario|zelda` builds an annotated ELF (gitignored `build/re/`) from the backups; names live in `tools/re/<device>.syms`. Commit only `.syms` (addresses/names/notes), never derived ELFs or listings. See `tools/re/README.md`.
- Zelda's `itcm_overlay_load` (0x0800f784) copies ITCM-linked code out of external flash at boot (NES core, GB core, menu UI; `itcm_copies` in `patches/xrefs.py` `LAYOUT`), so name those functions at their ITCM run address, not the 0x9030b4a0-0x90317230 load address. Code from 0x90317230 on runs in place. To patch ITCM code, edit the overlay's stock external bytes (`ZeldaGnW._itcm_menu_patch`); ITCM reaches our code through `_itcm_veneer` (veneers appended to the menu UI copy, since ITCM past 0xb7c0 is otherwise never loaded), and C calls into ITCM need `__attribute__((long_call))` prototypes (out of `bl` range). `_itcm_menu_patch` follows the overlay to wherever `_compact_ext` puts it, so a patch that needs final pointers runs after `fix_ext_refs()`. ITCM literal pools are shared between functions (e.g. `menu_rows_literal`), so to redirect one function's load, repoint a literal only it reads (grep the objdump for `@ <addr>`) or patch the `ldr`. Many shared HAL/SDK/emulator functions are byte-identical between the two ROMs, and the `.syms` give them the same names.
- `tools/emu/mario_menu_test.py` runs the patched build headless in GWemu via gwprov and injects buttons over GDB (keyboard input doesn't work for stock Mario). See `tools/emu/README.md`. Its `--internal` option boots an arbitrary image; that's how `GAME_SELECT_PALETTE` (the menu UI colors, `patches/tileset.py`) was measured, by drawing a 16-stripe test icon and sampling the screenshot.
- `tools/emu/zelda_menu_test.py` boots a Zelda build and launches all 10 game-select variants, checking `g_app_id` (2 first-boot, 4 clock, 0x12 menu, 5 timer, 6-9 LoZ, 11-14 LA, 15 Vermin); `--dual-boot spin|erased` checks `--game-select-dual-boot`. Shared harness code is `tools/emu/emu_driver.py`. Zelda's menu palette (`ZELDA_GAME_SELECT_PALETTE`) was read from its ARGB color table in RAM (0x24090800 holds entry 0xF0). gwprov keeps one backup pair per directory, so `make_profile` registers each model from `build/emu/<model>-backups/` symlinks.
- `tools/emu/ram_probe.py` (never-written RAM, `--device zelda` too) and `tools/emu/coverage.py` (per-mode function coverage, the dead-code finder) also live there. `coverage.py` takes over 10 minutes, so run it in the background. All GWemu tools need a build with an external image; `--internal-only` leaves `build/external_flash_patched.bin` empty. Don't repaint RAM between harness steps that holds live stock state: LoZ2 JP won't launch over a non-zero `app_state_loz2_jp`, and `nvram_ram` gets corrupted. A harness interrupted mid-run leaves a halted GWemu that makes the next `start_instance` fail ("already has GWemu pid N"); stop it with `gwprov.gwemu_manager.stop_instance(pid=N)`.
- Relocating stock code: `patches/xrefs.py` maps every reference into stock internal code (`tools/re/xrefs.py` prints it); `patches/relocate.py` moves a range of the patched image (`--relocate-stock=0x08120000` moves all of Mario's or Zelda's stock code to bank 2; `--extended --relocate-stock=0x08020000` moves it inside bank 1, past our code, reserving the range in `int_space`; both verified in GWemu with `tools/emu/mario_menu_test.py`/`zelda_menu_test.py --stock-offset` and, for Mario, `tools/emu/shift_test.py`, see `tools/emu/README.md`). Zelda's ITCM code pointers live in the external overlay, so `fix_ram_images()` runs before `_finish_ext()` compresses it. Missed references so far came from places the scan didn't look: external data tables, `ldr`/`add pc` offsets, the init table at `0x0801807c`. A pointer the map lacks shows up as a fault in bank 1 (`udf`).
- Emulator timing isn't a fixed number of frames (the stock UI ignores presses during animations, and relocated builds needed different counts), so emulator tests wait on firmware state (`press_until`/`wait_until` in `mario_menu_test.py`) instead of fixed frame counts.
- Moving external data: `Device.fix_ext_refs()` (called once in each `patch()`, after the last move) rewrites every stock pointer into moved data that `xrefs.ext_refs()` finds (internal literals and data, vector-table words, ITCM literals, DTCM `.data`, external literals and `ext_pointer_tables`). A move's `reference` argument is only an assertion against that scan, except for pointers in our code. A pointer-like data word into moved data that isn't classified fails the build; classify it in `LAYOUT` (`ext_pointer_tables` or `not_ext_pointers`).
- Removing stock code: make it unreachable (check BL/B.W callers, literal pointers, and the static-constructor table at `0x08017e00`), also scan the decompressed rwdata for pointers (`RWData` in `patches/firmware.py`) and check for helpers called from other removable code; then `self.free_int(start, end)`; `move_to_int` reuses the hole and the range is filled with `udf`. See `--no-factory-test` in `patches/mario.py`.
- Mario sleep screen (`g_gnw_mode` 0x80): stock resets the idle timer every frame while USB is connected, so on hardware it needs `--sleep-time=5 --sleep-on-usb`; GWemu has no USB, so `--sleep-time=5` alone shows it after ~5 s idle on the clock. The first-boot PRESS TIME screen also draws a sleep image (juggling).
- To catch a code path in GWemu (e.g. a breakpoint in ITCM during SMB1), keep injecting every poll while the extra breakpoint is armed (resume, stop at either breakpoint, step off, repeat). Letting the target run free for even a moment lets GWemu's stuck TIME through, and the firmware drops back to the clock. Breakpoints in ITCM code do work.
- In ad-hoc GDB scripts, resuming from a breakpoint re-hits it: clear it, `step`, re-arm, then resume (`Driver` does this). A breakpoint set on the bootloader survives `NVIC_SystemReset`, so a script can stop right after a reset.
- The harnesses don't clear their screenshot directories (`build/emu/shift/` keeps an old `contact.png` and `*_FAIL.png` from earlier runs); check mtimes before trusting a screenshot.
- GWemu doesn't write flash back to the profile's `extflash.bin`; to see what the firmware wrote, read the memory-mapped address over GDB (`Driver.read(0x90xxxxxx, n)`, unencrypted builds). Zelda writes LA save states only on power-off, so GWemu never exercises them; Zelda's backdrop (`backdrop_tick`) shows on the first-boot PRESS TIME screen, so the harness's first screenshot covers it.
- GWemu can't run past the sleep screen: once `g_gnw_mode` gets 0x80 the firmware parks in `wfe` at 0x0800325E and `main_loop_tick` never runs again, so the sleep-screen timer and mario song (`--mario-song-time`, 0x08006FC4) can't be exercised there. To check a patch to such code, run just that snippet in unicorn (not a repo dependency; use a scratch venv) on the stock and patched images and compare RAM writes/registers, then confirm on hardware.

### Adding Custom Functionality
1. Find hook point in stock ROM (function calls)
2. Stock functions: name it in `tools/re/<device>.syms` and add a plain prototype for `stock_<device>_<name>` (a `.syms` name starting `stock_` drops it: `stock_mario_Reset_Handler`) to the device header in `Core/Inc/`. The build generates `build/stock_symbols.s` (an absolute `FUNC` symbol per `.syms` function, via `patches/syms.py`) and links with `--emit-relocs`, so every call is a relocation `patches/relocate.py` can fix. Stock RAM addresses stay `static` constants in the header. Symbols the patcher looks up by name (e.g. `SMB1_ROM`) must be defined in a `.c` file. Patch code gets a stock symbol's image offset with `self.internal.sym(name)`; give every code patch an `expect="<len>:<crc32>"` of the stock bytes (a hash, so no stock bytes are committed; pass `"4:0"` and copy the value from the error), and re-record `tests/golden.json` (`UPDATE_GOLDEN=1`) when output is meant to change
3. Implement custom function in `Core/Src/<device>.c` (`main.c` if both models share it)
4. Add `-Wl,--undefined=my_function` to LDFLAGS in Makefile if needed
5. Add patch definition in the device's Python class

## Required Files

Stock firmware backups must be in repo root before patching:
- `internal_flash_backup_mario.bin` / `internal_flash_backup_zelda.bin`
- `flash_backup_mario.bin` / `flash_backup_zelda.bin`

Extract these using gnwmanager: https://github.com/BrianPugh/gnwmanager
