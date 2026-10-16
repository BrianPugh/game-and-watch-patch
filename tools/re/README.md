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

Hopper's pseudocode for large, heavily-predicated functions (e.g. Mario's
`main_loop_tick`) can explode in size; read the objdump assembly instead.

## `.syms` format

```
# addr      kind  name                     # notes
0x0800c858  func  game_select_handle_input # called once at 0x0800cb18
0x20001044  data  g_gnw_mode               # u16: 0x02 clock, 0x08 BALL, ...
```

Mark anything not confirmed from code as `UNVERIFIED` in its note.
