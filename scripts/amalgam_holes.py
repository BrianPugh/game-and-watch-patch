"""Pack our code into freed stock code: the amalgam with --sd-bootloader.

The Makefile links the amalgam's ELF twice. The first link (the plain amalgam
layout, ``build/pass1/``) tells which input sections ``--gc-sections`` keeps
and their sizes (its map file); this script assigns them to the holes in
``patches/amalgam.py`` ``SD_HOLES`` and writes ``build/amalgam_text.ld``, which
the second link uses. What it doesn't place goes to ``.text`` past Zelda's
code, before the SD bootloader (the script asserts that it fits).

It packs largest first into the hole with the least room that fits (best
fit), and keeps as many of the largest holes empty as it can: the patcher
puts the compressed RAM images and relocated data in what's left, and those
need contiguous room. Objects Zelda's stock code reaches only go into holes
outside Zelda's relocation source range (``SD_HOLES``' last field), and our
data the patchers write only into holes past ``SD_PRELOADED``.

    python -m scripts.amalgam_holes build/gw_patch.map build/amalgam_text.ld
"""

import argparse
import re
from dataclasses import dataclass
from pathlib import Path

from elftools.elf.elffile import ELFFile

from patches.amalgam import SD_BOOTLOADER, SD_HOLES, SD_PRELOADED

BANK1 = (0x0800_0000, 0x0804_0000)
# Objects Zelda's (patched) stock code calls or points into (basenames).
ZELDA_REACHES = ("main.o", "game_select.o", "ips.o")
# Our data the patchers write, or fix pointers in (patches/amalgam.py
# check_patcher_data fails the patch step on a missing one).
PATCHER_DATA = (
    "LOZ2_CHR",
    "MARIO_GAME_SELECT_DUAL_BOOT_ICON",
    "SMB1_GRAPHIC_MODS",
    "SMB1_ROM",
    "ZELDA_GAME_SELECT_DUAL_BOOT_ICON",
)


def zelda_reaches(obj):
    return obj in ZELDA_REACHES or obj.endswith("zelda.o")


@dataclass
class Item:
    obj: str  # path as the linker saw it
    name: str  # input section name
    size: int
    align: int

    @property
    def zelda(self):
        return zelda_reaches(Path(self.obj).name)

    @property
    def patcher_data(self):
        return self.name.split(".")[-1] in PATCHER_DATA

    def fits(self, hole):
        start, _, zelda = hole
        if self.zelda and not zelda:
            return False
        return start >= SD_PRELOADED or not self.patcher_data

    @property
    def room(self):
        """Bytes it takes in a hole: items go in by descending alignment, so
        rounding each one up to its own alignment leaves no padding."""
        return -(-self.size // self.align) * self.align

    @property
    def spec(self):
        return f"*/{Path(self.obj).name}({self.name})"


def map_items(map_path):
    """The .text/.rodata input sections of our code in flash."""
    lines = Path(map_path).read_text().split("\n")
    lines = lines[lines.index("Linker script and memory map") :]
    out = []
    output_section = None
    for i, line in enumerate(lines):
        m = re.match(r"^(\.\S+)", line)
        if m:
            output_section = m.group(1)
            continue
        if output_section not in (".text", ".rodata"):
            continue
        m = re.match(
            r"^ (\.(?:text|rodata)\S*)(?:\s+(0x\S+)\s+(0x\S+)\s+(\S+))?$", line
        )
        if not m:
            continue
        name, addr, size, obj = m.groups()
        if addr is None:
            m = re.match(r"^\s+(0x\S+)\s+(0x\S+)\s+(\S+)$", lines[i + 1])
            if not m:
                continue
            addr, size, obj = m.groups()
        addr, size = int(addr, 16), int(size, 16)
        if size and BANK1[0] <= addr < BANK1[1] and obj.endswith(".o"):
            out.append(Item(obj, name, size, 0))
    aligns = {}
    for item in out:
        if item.obj not in aligns:
            with open(item.obj, "rb") as f:
                elf = ELFFile(f)
                aligns[item.obj] = {
                    s.name: s["sh_addralign"] for s in elf.iter_sections()
                }
        item.align = max(1, aligns[item.obj][item.name])
    return out


def pack(items, holes):
    """{hole: [items]} and the items that didn't fit."""
    room = {h: h[1] - h[0] for h in holes}
    placed = {h: [] for h in holes}
    left = []
    for item in sorted(items, key=lambda i: (-i.room, i.obj, i.name)):
        fits = [h for h in holes if room[h] >= item.room and item.fits(h)]
        if not fits:
            left.append(item)
            continue
        h = min(fits, key=lambda h: (room[h], h[0]))
        room[h] -= item.room
        placed[h].append(item)
    return placed, left


def assign(items, holes=SD_HOLES):
    """Pack, keeping the largest holes empty while everything still fits."""
    holes = list(holes)
    _, left = pack(items, holes)
    spill = sum(i.room for i in left)
    for h in sorted(holes, key=lambda h: (h[0] - h[1], h[0])):
        _, left = pack(items, [x for x in holes if x is not h])
        if sum(i.room for i in left) <= spill:
            holes.remove(h)
    return pack(items, holes)


def linker_script(placed):
    ld = "/* Generated by scripts/amalgam_holes.py */\n"
    ld += (
        "/* objcopy's .bin starts at the first section; make that bank 1's start. */\n"
    )
    ld += ".bin_origin : { LONG(0) } >BIN_ORIGIN\n"
    for hole, items in sorted(placed.items()):
        if not items:
            continue
        specs = "".join(
            f"    {i.spec}\n" for i in sorted(items, key=lambda i: -i.align)
        )
        ld += f".hole_{hole[0]:08X} :\n{{\n{specs}}} >HOLE_{hole[0]:08X}\n"
    ld += "INCLUDE STM32H7B0VBTx_FLASH_text.ld\n"
    ld += (
        f"ASSERT(__flash_end__ <= 0x{SD_BOOTLOADER:08X}, "
        '"our code and data run into the SD bootloader")\n'
    )
    return ld


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("map", type=Path, help="the first link's map file")
    parser.add_argument("out", type=Path, help="the linker script to write")
    args = parser.parse_args()
    items = map_items(args.map)
    placed, left = assign(items)
    args.out.write_text(linker_script(placed))
    used = sum(i.room for items in placed.values() for i in items)
    room = {h: h[1] - h[0] for h in SD_HOLES}
    for h, items in placed.items():
        room[h] -= sum(i.room for i in items)
    print(
        f"Packed {used} bytes of our code into freed stock code, "
        f"{sum(i.room for i in left)} past Zelda's code; "
        f"largest holes left: {sorted(room.values(), reverse=True)[:4]}"
    )


if __name__ == "__main__":
    main()
