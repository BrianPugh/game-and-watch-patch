# Reverse-engineering tooling

Helpers for analyzing the stock firmware in a disassembler (Hopper, Ghidra,
objdump, ...).

## What lives where

| File | Committed? | Contents |
|------|------------|----------|
| `tools/re/<device>.syms` | yes | Addresses, names, and notes. Our annotations only; no firmware bytes. |
| `tools/re/build_elf.py` | yes | Builds an analysis ELF from *your* stock backups. |
| `build/re/<device>.elf` | **no** (gitignored) | Derived from Nintendo's firmware. Never commit it, or disassembly listings made from it. |

## Usage

```bash
python tools/re/build_elf.py --device=mario   # or --device=zelda
arm-none-eabi-objdump -d --start-address=0x080002cc --stop-address=0x08018004 build/re/mario.elf
```

The ELF maps internal flash at `0x0800_0000`, the decrypted external flash at
`0x9000_0000`, and each LZ77-compressed RWDATA init block at its RAM
destination (Mario: ITCM code at `0x1E5C`, DTCM data at `0x2000_0800`), so
cross-region calls and literal pools resolve. Function symbols come from the
`.syms` file plus auto-detected vector-table handlers, `ldr.w pc, [pc]`
veneers, and literal-pool pointers to external-flash function prologues.

## Code references (`xrefs.py`)

```bash
python tools/re/xrefs.py --device=mario [--units] [--json build/re/mario_xrefs.json]
```

Recursive-descent disassembly (capstone) of internal flash, the ITCM code and
the external-flash code, seeded from the vector table, the static-constructor
table, `.syms` and every Thumb pointer it finds. It prints what relocating
internal code has to handle: byte classification, the units that must move
together (armcc shares literal pools across a module), the branches between
units, and every absolute or self-relative reference into internal code
(vector table, constructors, RWDATA table, literal pools, pointer tables in
flash, ITCM/external veneers, DTCM `.data`). It also lists `push` prologues
nothing references: dead code, or reached some way it doesn't model. Mario
only so far (`LAYOUT` in the script).

## Relocation test (`shift_image.py`)

```bash
python tools/re/shift_image.py --device=mario          # -> build/re/shift/
build/gwprov-venv/bin/python tools/emu/shift_test.py --gwemu-bin /path/to/gwemu
```

Moves the whole stock image to bank 2 (`0x08100000`) using the `xrefs.py`
map: every absolute reference gets the offset added, plus the `SCB->VTOR`
immediate in `SystemInit`. Relative branches and PC-relative loads need no
change. The rwdata blocks are re-encoded as literal-only LZ77 so the ITCM and
DTCM pointers inside them can be patched. Bank 1 keeps only the vector table
and is otherwise `udf`, so any reference the map misses faults. This is the
completeness test for the map (and the first building block for running
2 stock firmwares side by side).

Hopper's pseudocode for large, heavily-predicated functions (e.g. Mario's
`main_loop_tick`) can explode in size; read the objdump assembly instead.

## `.syms` format

```
# addr      kind  name                     # notes
0x0800c858  func  game_select_handle_input # called once at 0x0800cb18
0x20001044  data  g_gnw_mode               # u16: 0x02 clock, 0x08 BALL, ...
```

Mark anything not confirmed from code as `UNVERIFIED` in its note.
