#!/usr/bin/env python3
"""Build an annotated ELF of the stock firmware for disassemblers (Hopper, Ghidra, objdump).

The ELF maps every region the stock firmware runs from at its real address:

* internal flash at ``0x0800_0000``
* decrypted external flash at ``0x9000_0000``
* each RWDATA init block (LZ77-decompressed) at its RAM destination (ITCM/DTCM)
* the code/data the firmware copies from external flash to ITCM/DTCM at boot
  (``itcm_copies`` / ``dtcm_copies`` in ``patches/xrefs.py`` ``LAYOUT``)

Symbols come from ``tools/re/<device>.syms`` plus a few auto-detected ones
(vector-table handlers and ``ldr.w pc, [pc]`` long-branch veneers).

The output is derived from your stock firmware backups and must never be
committed; it is written to the gitignored ``build/`` directory. Only the
``.syms`` files (addresses, names, notes) belong in the repo.

Usage:
    python tools/re/build_elf.py --device=mario
    arm-none-eabi-objdump -d build/re/mario.elf > build/re/mario.S
"""

import argparse
import struct
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from patches import Device  # noqa: E402
from patches.elf_image import link  # noqa: E402
from patches.syms import parse_syms  # noqa: E402
from patches.xrefs import EXT_BASE, LAYOUT, extract_rwdata, itcm_image  # noqa: E402

INT_BASE = 0x0800_0000
VECTOR_TABLE_END = 0x2CC  # STM32H7B0 vector table size; identical on both devices.


def i32(data, offset):
    return struct.unpack_from("<I", data, offset)[0]


def s32(data, offset):
    v = i32(data, offset)
    return v - (1 << 32) if v & 0x8000_0000 else v


def is_push_prologue(data, offset):
    """``push {..., lr}`` (16-bit) or ``stmdb sp!, {..., lr}`` (32-bit)."""
    if offset < 0 or offset + 4 > len(data):
        return False
    hw = struct.unpack_from("<H", data, offset)[0]
    return (hw & 0xFF00) == 0xB500 or hw == 0xE92D


def auto_funcs(internal, external):
    """Vector-table handlers, ``ldr.w pc, [pc]`` veneers (plus their targets), and
    word-aligned Thumb pointers in internal flash that land on a function prologue
    in external flash (e.g. literal-pool function pointers / callback tables)."""
    funcs = {}
    for i in range(1, VECTOR_TABLE_END // 4):
        v = i32(internal, i * 4)
        if v & 1 and INT_BASE <= v < INT_BASE + len(internal):
            funcs.setdefault(v & ~1, f"vector{i}_handler")
    for off in range(VECTOR_TABLE_END, len(internal) - 8, 2):
        if internal[off : off + 4] == b"\xdf\xf8\x00\xf0":  # ldr.w pc, [pc, #0]
            target = i32(internal, off + 4)
            if target & 1 and (target & 0xFF00_0000) in (INT_BASE, EXT_BASE):
                target &= ~1
                funcs.setdefault(INT_BASE + off, f"veneer_{target:08x}")
                funcs.setdefault(target, f"sub_{target:08x}")
    for off in range(0, len(internal) - 4, 4):
        target = i32(internal, off)
        if target & 1 and EXT_BASE <= target < EXT_BASE + len(external):
            if is_push_prologue(external, (target & ~1) - EXT_BASE):
                funcs.setdefault(target & ~1, f"sub_{target & ~1:08x}")
    return funcs


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--device", required=True, choices=sorted(Device.registry))
    parser.add_argument(
        "--internal", type=Path, help="Default: internal_flash_backup_<device>.bin"
    )
    parser.add_argument(
        "--external", type=Path, help="Default: flash_backup_<device>.bin"
    )
    parser.add_argument("--syms", type=Path, help="Default: tools/re/<device>.syms")
    parser.add_argument("--out-dir", type=Path, default=REPO / "build" / "re")
    parser.add_argument(
        "--prefix", default="arm-none-eabi-", help="GNU toolchain prefix."
    )
    args = parser.parse_args()

    device_cls = Device.registry[args.device]
    internal_path = args.internal or REPO / f"internal_flash_backup_{args.device}.bin"
    external_path = args.external or REPO / f"flash_backup_{args.device}.bin"
    syms_path = args.syms or Path(__file__).parent / f"{args.device}.syms"
    out = args.out_dir
    work = out / f"{args.device}_work"
    work.mkdir(parents=True, exist_ok=True)

    internal = internal_path.read_bytes()
    key = internal[device_cls.Int.KEY_OFFSET : device_cls.Int.KEY_OFFSET + 16]
    nonce = internal[device_cls.Int.NONCE_OFFSET : device_cls.Int.NONCE_OFFSET + 8]
    external = device_cls.Ext(external_path)  # Verifies the stock hash.
    external.crypt(key, nonce)

    # (section name, base address, data, is_code)
    regions = [
        (".intflash", INT_BASE, internal, True),
        (".extflash", EXT_BASE, bytes(external), True),
    ]
    rwdata = extract_rwdata(device_cls, internal)
    layout = LAYOUT.get(args.device, {})
    # ITCM as it is after boot: the code the firmware copies there from external
    # flash (LAYOUT itcm_copies) plus the ITCM rwdata block.
    itcm = itcm_image(layout, bytes(external), rwdata)
    if itcm:
        regions.append((".itcm", 0, itcm, True))
        print(f"itcm: 0x00000000 len=0x{len(itcm):X} (code)")
    for n, (dst, data) in enumerate(rwdata):
        if dst < 0x0001_0000:
            continue  # part of .itcm
        regions.append((f".rwdata{n}", dst, data, False))
        print(f"rwdata{n}: 0x{dst:08X} len=0x{len(data):X} (data)")
    for n, (src, dst, size) in enumerate(layout.get("dtcm_copies", [])):
        data = bytes(external)[src - EXT_BASE : src - EXT_BASE + size]
        regions.append((f".dtcmcopy{n}", dst, data, False))
        print(f"dtcmcopy{n}: 0x{dst:08X} len=0x{size:X} (data copied from 0x{src:08X})")

    funcs = auto_funcs(internal, bytes(external))
    user_funcs, datas = parse_syms(syms_path)
    funcs.update(user_funcs)

    # ARM mapping symbols: vector table is data, internal flash after it is code,
    # external flash is data except at known functions.
    mapping = [
        (INT_BASE, "$d"),
        (INT_BASE + VECTOR_TABLE_END, "$t"),
        (EXT_BASE, "$d"),
    ]
    for _, base, _, is_code in regions[2:]:
        mapping.append((base, "$t" if is_code else "$d"))
    mapping += [
        (addr, "$t") for addr in funcs if EXT_BASE <= addr < EXT_BASE + len(external)
    ]

    out_elf = out / f"{args.device}.elf"
    link(
        out_elf,
        regions,
        funcs.items(),
        datas.items(),
        mapping,
        entry=i32(internal, 4) & ~1,
        work=work,
        prefix=args.prefix,
    )

    print(f"Wrote {out_elf} ({len(funcs)} functions, {len(datas)} data symbols)")


if __name__ == "__main__":
    main()
