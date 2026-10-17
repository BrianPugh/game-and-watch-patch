"""Link raw memory images into an ELF with symbols, for disassemblers and gdb.

Each region becomes a loaded section at its address (``objcopy -I binary``,
then ``ld``), the rest of RAM gets NOLOAD sections so globals there have a
segment, and symbols are added with ``objcopy --add-symbol``. Used by
``tools/re/build_elf.py`` (the stock firmware) and ``patch.py`` (the patched
firmware, ``patches/manifest.py``).
"""

import os
import shutil
import subprocess
from pathlib import Path

# RAM regions with no initialized data get NOLOAD sections so that
# disassemblers create segments for globals living there.
RAM_REGIONS = [
    (0x0000_0000, 0x0001_0000),  # ITCM
    (0x2000_0000, 0x2002_0000),  # DTCM
    (0x2400_0000, 0x2410_0000),  # AXI SRAM
    (0x3000_0000, 0x3002_0000),  # AHB SRAM1/2
]


def toolchain_prefix():
    """``arm-none-eabi-`` (under ``GCC_PATH`` if set), or None if not installed."""
    prefix = "arm-none-eabi-"
    if os.environ.get("GCC_PATH"):
        prefix = str(Path(os.environ["GCC_PATH"]) / prefix)
    return prefix if shutil.which(prefix + "objcopy") else None


def in_ram(addr):
    return any(lo <= addr < hi for lo, hi in RAM_REGIONS)


def link(out, regions, funcs, datas, mapping, entry, work, prefix="arm-none-eabi-"):
    """Write the ELF ``out``.

    ``regions``: ``(section name, address, data, is_code)``, not overlapping.
    ``funcs``/``datas``: ``(address, name)`` pairs (functions without the
    Thumb bit).
    A symbol outside the regions must be in RAM (it's emitted absolute).
    ``mapping``: ``(address, "$t" or "$d")`` ARM mapping symbols (code/data
    from there on) inside the regions. ``entry``: the entry address.
    ``work``: a scratch directory.
    """
    work = Path(work)
    work.mkdir(parents=True, exist_ok=True)

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
                f"{prefix}objcopy",
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
    (work / "image.ld").write_text(
        "SECTIONS {\n"
        + "\n".join(lines)
        + "\n  /DISCARD/ : { *(.ARM.attributes) }\n}\n"
    )
    linked = work / "linked.elf"
    subprocess.run(
        [
            f"{prefix}ld",
            "-nostdlib",
            "--no-warn-rwx-segments",
            "-e",
            f"0x{entry:08x}",
            "-T",
            "image.ld",
            "-o",
            linked.name,
        ]
        + [o.name for o in objs],
        check=True,
        cwd=work,
    )

    def section_of(addr):
        for name, base, data, _ in regions:
            if base <= addr < base + len(data):
                return name, addr - base
        if in_ram(addr):
            return None, None  # NOLOAD filler; an absolute symbol.
        raise ValueError(f"0x{addr:08X} is not in any mapped region")

    args = ["--strip-all"]
    for addr, sym in sorted(mapping):
        sec, off = section_of(addr)
        args += ["--add-symbol", f"{sym}={sec}:0x{off:x},local"]
    for addr, name in sorted(funcs):
        sec, off = section_of(addr)
        if sec is None:
            args += ["--add-symbol", f"{name}=0x{addr | 1:x},global,function"]
        else:
            args += ["--add-symbol", f"{name}={sec}:0x{off | 1:x},global,function"]
    for addr, name in sorted(datas):
        sec, off = section_of(addr)
        if sec is None:
            args += ["--add-symbol", f"{name}=0x{addr:x},global,object"]
        else:
            args += ["--add-symbol", f"{name}={sec}:0x{off:x},global,object"]
    # Thousands of symbols: pass them in a response file.
    rsp = work / "symbols.rsp"
    rsp.write_text("\n".join(args) + "\n")
    out = Path(out)
    subprocess.run(
        [f"{prefix}objcopy", f"@{rsp.resolve()}", str(linked), str(out)], check=True
    )

    # e_flags: EABI v5, hard-float ABI. Hopper/Ghidra use this to pick Thumb-2 decoding.
    elf = bytearray(out.read_bytes())
    elf[0x24:0x28] = (0x0500_0400).to_bytes(4, "little")
    out.write_bytes(elf)
