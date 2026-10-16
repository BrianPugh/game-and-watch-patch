#!/usr/bin/env python3
"""Build an annotated ELF of the stock firmware for disassemblers (Hopper, Ghidra, objdump).

The ELF maps every region the stock firmware runs from at its real address:

* internal flash at ``0x0800_0000``
* decrypted external flash at ``0x9000_0000``
* each RWDATA init block (LZ77-decompressed) at its RAM destination (ITCM/DTCM)

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
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from patches import Device  # noqa: E402
from patches.xrefs import extract_rwdata, parse_syms  # noqa: E402,F401

INT_BASE = 0x0800_0000
EXT_BASE = 0x9000_0000
VECTOR_TABLE_END = 0x2CC  # STM32H7B0 vector table size; identical on both devices.

# RAM regions with no initialized data get NOLOAD sections so that
# disassemblers create segments for globals living there.
RAM_REGIONS = [
    (0x0000_0000, 0x0001_0000),  # ITCM
    (0x2000_0000, 0x2002_0000),  # DTCM
    (0x2400_0000, 0x2410_0000),  # AXI SRAM
]


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


def run(*cmd):
    subprocess.run([str(c) for c in cmd], check=True)


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
    for n, (dst, data) in enumerate(extract_rwdata(device_cls, internal)):
        is_itcm = dst < 0x0001_0000
        regions.append((f".rwdata{n}", dst, data, is_itcm))
        print(
            f"rwdata{n}: 0x{dst:08X} len=0x{len(data):X} ({'ITCM code' if is_itcm else 'data'})"
        )

    # Convert each region into an object file.
    objs = []
    for name, _, data, is_code in regions:
        bin_path = work / f"{name[1:]}.bin"
        obj_path = work / f"{name[1:]}.o"
        bin_path.write_bytes(data)
        flags = "contents,alloc,load," + ("readonly,code" if is_code else "data")
        # cwd=work keeps the auto-generated _binary_* symbol names short.
        subprocess.run(
            [
                f"{args.prefix}objcopy",
                "-I",
                "binary",
                "-O",
                "elf32-littlearm",
                "-B",
                "arm",
                f"--rename-section=.data={name},{flags}",
                bin_path.name,
                obj_path.name,
            ],
            check=True,
            cwd=work,
        )
        objs.append(obj_path)

    # Linker script: loaded regions plus NOLOAD fillers for the remaining RAM.
    lines = []
    for name, base, _, _ in regions:
        lines.append(f"  {name} 0x{base:08x} : {{ {name[1:]}.o({name}) }}")
    for lo, hi in RAM_REGIONS:
        cursor = lo
        for _, base, data, _ in sorted(
            (r for r in regions if lo <= r[1] < hi), key=lambda r: r[1]
        ):
            if base > cursor:
                lines.append(
                    f"  .ram_{cursor:08x} 0x{cursor:08x} (NOLOAD) : {{ . = . + 0x{base - cursor:x}; }}"
                )
            cursor = max(cursor, base + len(data))
        if hi > cursor:
            lines.append(
                f"  .ram_{cursor:08x} 0x{cursor:08x} (NOLOAD) : {{ . = . + 0x{hi - cursor:x}; }}"
            )
    reset = i32(internal, 4)
    (work / "re.ld").write_text(
        f"Reset_Handler = 0x{reset:08x};\nENTRY(Reset_Handler)\nSECTIONS {{\n"
        + "\n".join(lines)
        + "\n  /DISCARD/ : { *(.ARM.attributes) }\n}\n"
    )
    linked = work / "linked.elf"
    subprocess.run(
        [
            f"{args.prefix}ld",
            "-nostdlib",
            "--no-warn-rwx-segments",
            "-T",
            "re.ld",
            "-o",
            linked.name,
        ]
        + [o.name for o in objs],
        check=True,
        cwd=work,
    )

    # Symbols.
    def section_of(addr):
        for name, base, data, _ in regions:
            if base <= addr < base + len(data):
                return name, addr - base
        for lo, hi in RAM_REGIONS:
            if lo <= addr < hi:
                return None, None  # NOLOAD filler; handled below.
        raise ValueError(f"0x{addr:08X} is not in any mapped region")

    funcs = auto_funcs(internal, bytes(external))
    user_funcs, datas = parse_syms(syms_path)
    funcs.update(user_funcs)

    objcopy_args = ["--strip-all"]
    # ARM mapping symbols: vector table is data, internal flash after it is code,
    # external flash is data except at known functions.
    mapping = [
        ("$d", ".intflash", 0),
        ("$t", ".intflash", VECTOR_TABLE_END),
        ("$d", ".extflash", 0),
    ]
    for name, _, _, is_code in regions[2:]:
        mapping.append(("$t" if is_code else "$d", name, 0))
    for sym, sec, off in mapping:
        objcopy_args += ["--add-symbol", f"{sym}={sec}:0x{off:x},local"]
    for addr, name in sorted(funcs.items()):
        sec, off = section_of(addr)
        objcopy_args += ["--add-symbol", f"{name}={sec}:0x{off | 1:x},global,function"]
        if sec == ".extflash":
            objcopy_args += ["--add-symbol", f"$t={sec}:0x{off:x},local"]
    for addr, name in sorted(datas.items()):
        sec, off = section_of(addr)
        if sec is None:  # Uninitialized RAM; emit an absolute symbol.
            objcopy_args += ["--add-symbol", f"{name}=0x{addr:x},global,object"]
        else:
            objcopy_args += ["--add-symbol", f"{name}={sec}:0x{off:x},global,object"]

    out_elf = out / f"{args.device}.elf"
    run(f"{args.prefix}objcopy", *objcopy_args, linked, out_elf)

    # e_flags: EABI v5, hard-float ABI. Hopper/Ghidra use this to pick Thumb-2 decoding.
    elf = bytearray(out_elf.read_bytes())
    elf[0x24:0x28] = (0x0500_0400).to_bytes(4, "little")
    out_elf.write_bytes(elf)

    print(f"Wrote {out_elf} ({len(funcs)} functions, {len(datas)} data symbols)")


if __name__ == "__main__":
    main()
