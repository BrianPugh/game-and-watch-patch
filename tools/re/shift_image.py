#!/usr/bin/env python3
"""Build a stock Mario image that runs from another address (relocation test).

Shifts the whole stock internal image by ``--delta`` and fixes every absolute
reference into it that ``tools/re/xrefs.py`` finds. Relative branches and
PC-relative loads need no change because the whole image moves together.
Bank 1 keeps only a copy of the shifted vector table (for reset); the rest of
it is ``udf``, so a reference the map missed faults (or reads garbage)
instead of silently running the original.

The stock LZ77 rwdata blocks are re-encoded as literal runs so the ITCM and
DTCM pointers inside them can be patched.

Writes ``build/re/shift/{bank1,bank2,external}.bin`` (and unshifted copies in
``build/re/shift/stock/``); boot them with
``tools/emu/shift_test.py``.

Usage:
    python tools/re/shift_image.py --device=mario [--delta 0x100000]
"""

import argparse
import contextlib
import io
import struct
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from patches import Device  # noqa: E402
from patches import xrefs  # noqa: E402
from patches.compression import lz77_decompress  # noqa: E402

INT_BASE = xrefs.INT_BASE
EXT_BASE = xrefs.EXT_BASE

# Instructions that build an image address from an immediate: (addr, old, new asm).
IMMEDIATES = {
    "mario": [(0x0801_7B76, 0x0800_0000, "mov.w r2, #{}")],  # SystemInit: SCB->VTOR
}


def lz77_literal(data):
    """Encode ``data`` as stock-LZ77 literal runs (no back-references)."""
    out = bytearray()
    i = 0
    while len(data) - i >= 2:
        n = min(257, len(data) - i)
        out += bytes((0x00, n - 2)) + data[i : i + n]
        i += n
    if i < len(data):
        out += b"\x02" + data[i:]
    assert lz77_decompress(out) == data
    return bytes(out)


def thumb2_mov_imm(reg, value):
    """``mov.w reg, #value`` for a Thumb-2 modified immediate (byte rotated)."""
    for rot in range(8, 32):
        imm8 = ((value << rot) | (value >> (32 - rot))) & 0xFFFF_FFFF
        if imm8 < 0x100 and imm8 & 0x80:
            imm12 = (rot << 7) | (imm8 & 0x7F)
            hw1 = 0xF04F | ((imm12 >> 11) & 1) << 10
            hw2 = ((imm12 >> 8) & 7) << 12 | reg << 8 | (imm12 & 0xFF)
            return struct.pack("<HH", hw1, hw2)
    raise ValueError(f"0x{value:08X} isn't a Thumb-2 modified immediate")


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--device", default="mario", choices=sorted(xrefs.LAYOUT))
    parser.add_argument("--delta", type=lambda s: int(s, 0), default=0x10_0000)
    parser.add_argument("--out-dir", type=Path, default=REPO / "build/re/shift")
    args = parser.parse_args()
    delta = args.delta
    if delta != 0x10_0000:
        raise SystemExit("Only --delta 0x100000 (bank 2) is wired up for GWemu so far.")

    with contextlib.redirect_stdout(io.StringIO()):
        a = xrefs.analyze(args.device)
    internal, refs = a.internal, a.refs
    device_cls = Device.registry[args.device]
    key = internal[device_cls.Int.KEY_OFFSET : device_cls.Int.KEY_OFFSET + 16]
    nonce = internal[device_cls.Int.NONCE_OFFSET : device_cls.Int.NONCE_OFFSET + 8]
    ext = device_cls.Ext(REPO / f"flash_backup_{args.device}.bin")
    ext.crypt(key, nonce)

    image = bytearray(internal)
    table = device_cls.Int.RWDATA_OFFSET
    entries = []  # [field offset, dst, data]
    for i in range(table, table + device_cls.Int.RWDATA_LEN - 4, 16):
        data_off = i + 4 + struct.unpack_from("<i", image, i + 4)[0]
        length = struct.unpack_from("<I", image, i + 8)[0] >> 1
        dst = struct.unpack_from("<I", image, i + 12)[0]
        entries.append(
            [i, dst, bytearray(lz77_decompress(image[data_off : data_off + length]))]
        )

    def add(buf, off, what):
        old = struct.unpack_from("<I", buf, off)[0]
        struct.pack_into("<I", buf, off, old + delta)
        return old

    counts = {}
    for r in refs:
        kind, src, target = r["kind"], r["src"], r["target"]
        if "self-relative" in kind or kind == "pc-offset literal":
            continue  # Source and target both move with the image.
        if "verify" in kind and not kind.startswith("data pointer in flash"):
            continue  # Pointer-like data words outside code; left alone.
        if kind in (
            "vector",
            "literal",
            "pointer in flash data",
            "data pointer in flash data (verify)",
        ):
            old = add(image, src - INT_BASE, kind)
        elif kind in ("external literal", "external data pointer"):
            old = add(ext, src - EXT_BASE, kind)
        elif kind in ("ITCM literal", "DTCM .data"):
            blob = next((e for e in entries if e[1] <= src < e[1] + len(e[2])), None)
            if blob is not None:
                old = add(blob[2], src - blob[1], kind)
            elif (
                kind == "ITCM literal"
            ):  # ITCM below the rwdata block is copied from external flash.
                old = add(ext, src, kind)
            else:
                raise ValueError(f"no rwdata block holds 0x{src:08X}")
        else:
            raise ValueError(f"unhandled reference kind {kind!r}")
        assert old == target, (kind, hex(src), hex(old), hex(target))
        counts[kind] = counts.get(kind, 0) + 1

    for addr, old, _ in IMMEDIATES[args.device]:
        off = addr - INT_BASE
        assert image[off : off + 4] == thumb2_mov_imm(
            2, old
        ), f"unexpected bytes at 0x{addr:08X}"
        image[off : off + 4] = thumb2_mov_imm(2, old + delta)
        counts["immediate"] = counts.get("immediate", 0) + 1

    # Re-encode rwdata after the table (self-relative data offsets, length << 1).
    pos = table + device_cls.Int.RWDATA_LEN
    end_old = len(internal.rstrip(b"\xff"))
    image[pos:end_old] = b"\xff" * (end_old - pos)
    for field, dst, data in entries:
        enc = lz77_literal(bytes(data))
        image[pos : pos + len(enc)] = enc
        struct.pack_into("<i", image, field + 4, pos - (field + 4))
        flag = struct.unpack_from("<I", internal, field + 8)[0] & 1
        struct.pack_into("<I", image, field + 8, (len(enc) << 1) | flag)
        print(
            f"rwdata 0x{dst:08X}: {len(data)} B -> {len(enc)} B literal LZ77 at 0x{INT_BASE + pos:08X}"
        )
        pos += len(enc)
    assert pos <= len(image), "re-encoded rwdata doesn't fit"

    bank1 = bytearray(b"\xfe\xde" * (len(image) // 2))
    bank1[: xrefs.VECTOR_TABLE_END] = image[: xrefs.VECTOR_TABLE_END]

    ext.crypt(key, nonce)
    out = args.out_dir
    out.mkdir(parents=True, exist_ok=True)
    (out / "bank1.bin").write_bytes(bank1)
    (out / "bank2.bin").write_bytes(image + b"\xff" * (0x4_0000 - len(image)))
    (out / "external.bin").write_bytes(bytes(ext))
    # Unshifted reference images for checking the emulator harness itself.
    stock = out / "stock"
    stock.mkdir(exist_ok=True)
    (stock / "bank1.bin").write_bytes(internal)
    (stock / "bank2.bin").write_bytes(b"\xff" * 0x4_0000)
    (stock / "external.bin").write_bytes(
        (REPO / f"flash_backup_{args.device}.bin").read_bytes()
    )
    print(f"shifted by 0x{delta:X}; fixed:", counts)
    print(f"wrote {out}/bank1.bin, bank2.bin, external.bin")


if __name__ == "__main__":
    main()
