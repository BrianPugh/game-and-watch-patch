"""Relocate a range of (patched) stock internal code to another address.

The stock reference map comes from ``patches/xrefs.py``. Moving a range by
``delta`` needs:

* absolute words (vector table, literal pools, pointer tables, ITCM/DTCM
  rwdata, external and SRAM3 data, our code's ``R_ARM_ABS32`` relocations)
  pointing into the range: ``+ delta``.
* self-relative words (constructor, init and rwdata tables, ``ldr``/``add pc``
  offsets) whose word and target are on opposite sides of the boundary.
* 32-bit branches crossing the boundary, in either direction: re-encoded. That's
  stock code calling our hooks, and our code calling stock (ELF relocations;
  link with ``--emit-relocs``).

Relative branches and PC-relative loads inside the range need nothing.

It runs in 2 steps because the RAM images are compressed before the internal
image is final: ``fix_ram_images()`` before the rwdata/SRAM3 blobs are
written, ``apply()`` once everything else is patched.
"""

import contextlib
import io
import struct

import numpy as np
from elftools.elf.relocation import RelocationSection

from . import writelog, xrefs
from .layout import BANK2_BASE, BANK2_END
from .xrefs import Code

R_ARM_ABS32 = 2
R_ARM_THM_CALL = 10
R_ARM_THM_JUMP24 = 30


def _decode_branch(hw1, hw2):
    """(kind, offset) of a 32-bit Thumb BL / B.W / Bcc.W, else None."""
    if hw1 & 0xF800 != 0xF000 or not hw2 & 0x8000:
        return None
    s = (hw1 >> 10) & 1
    j1, j2 = (hw2 >> 13) & 1, (hw2 >> 11) & 1
    op = hw2 & 0xD000
    if op in (0xD000, 0x9000):  # BL, B.W (T4)
        i1, i2 = 1 ^ (j1 ^ s), 1 ^ (j2 ^ s)
        off = (
            (s << 24)
            | (i1 << 23)
            | (i2 << 22)
            | ((hw1 & 0x3FF) << 12)
            | ((hw2 & 0x7FF) << 1)
        )
        off -= s << 25
        return ("bl" if op == 0xD000 else "b.w"), off
    if op == 0x8000 and (hw1 >> 6) & 0xF < 0xE:  # Bcc.W (T3)
        off = (
            (s << 20)
            | (j2 << 19)
            | (j1 << 18)
            | (((hw1 >> 0) & 0x3F) << 12)
            | ((hw2 & 0x7FF) << 1)
        )
        off -= s << 21
        return "bcc.w", off
    return None


def _encode_branch(hw1, hw2, offset):
    """Re-encode the 32-bit branch (hw1, hw2) with a new byte ``offset``."""
    kind, _ = _decode_branch(hw1, hw2)
    if offset & 1:
        raise ValueError("odd branch offset")
    if kind == "bcc.w":
        if not -(1 << 20) <= offset < (1 << 20):
            raise ValueError(f"Bcc.W offset {offset:#x} out of range")
        s = (offset >> 20) & 1
        hw1 = 0xF000 | (s << 10) | (hw1 & 0x03C0) | ((offset >> 12) & 0x3F)
        hw2 = (
            (hw2 & 0xD000)
            | (((offset >> 18) & 1) << 13)
            | (((offset >> 19) & 1) << 11)
            | ((offset >> 1) & 0x7FF)
        )
        return hw1, hw2
    if not -(1 << 24) <= offset < (1 << 24):
        raise ValueError(f"branch offset {offset:#x} out of range")
    s = (offset >> 24) & 1
    i1, i2 = (offset >> 23) & 1, (offset >> 22) & 1
    j1, j2 = 1 ^ i1 ^ s, 1 ^ i2 ^ s
    hw1 = 0xF000 | (s << 10) | ((offset >> 12) & 0x3FF)
    hw2 = (hw2 & 0xD000) | (j1 << 13) | (j2 << 11) | ((offset >> 1) & 0x7FF)
    return hw1, hw2


class Relocation:
    """Move internal ``[lo, hi)`` of a patched device to ``dst``."""

    def __init__(self, device, lo, hi, dst, keep_vector_table=True):
        """``dst`` is in bank 2 (a separate image) or in this device's own
        internal image, past its code and data; either range is reserved now so
        nothing else is placed there.

        ``keep_vector_table``: the moved vector table also stays at the start
        of the image (boot and VTOR use 0x08000000). The amalgam sets it false:
        Mario's vector table is there."""
        if (dst - lo) % 0x1000:
            raise ValueError("relocation offset must be a multiple of 4 KB")
        if dst_in_bank2(dst):
            if dst + hi - lo > BANK2_END:
                raise ValueError(
                    f"0x{dst:08X}-0x{dst + hi - lo:08X} doesn't fit in bank 2 "
                    f"(0x{BANK2_BASE:08X}-0x{BANK2_END:08X})"
                )
            device.bank2_space.reserve(
                dst - BANK2_BASE, hi - lo, device.name, "stock code"
            )
        else:
            if dst < hi:
                raise ValueError(f"0x{dst:08X} overlaps the range it moves")
            region = device.int_space
            region.reserve(
                dst - region.base, hi - lo, device.name, "relocated stock code"
            )
        self.device = device
        self.keep_vector_table = keep_vector_table
        self.lo, self.hi, self.dst = lo, hi, dst
        self.delta = dst - lo
        self.map = device.stock_map
        self.counts = {}
        # writelog provenance of the moved range at dst, once applied.
        self.prov = None

    def moves(self, addr):
        return self.lo <= addr < self.hi

    def freed(self, addr):
        """In stock code the patcher removed."""
        base = self.device.internal.FLASH_BASE
        return self.device.is_freed_int(addr - base)

    def int_delta(self, offset):
        """How far the internal image's stock ``offset`` moves within the
        image: ``delta`` if it's in the range and the destination is in the
        same image, else 0."""
        base = self.device.internal.FLASH_BASE
        if dst_in_bank2(self.dst) or not self.moves(base + offset):
            return 0
        return self.delta

    def _count(self, kind):
        self.counts[kind] = self.counts.get(kind, 0) + 1

    def _fix_abs(self, buf, off, kind):
        v = struct.unpack_from("<I", buf, off)[0]
        if self.moves(v & ~1):
            _pack("<I", buf, off, v + self.delta)
            self._count(kind)

    def fix_ram_images(self):
        """Fix pointers in data that ends up compressed: rwdata blocks, SRAM3."""
        with writelog.step("Fix references for the relocated stock code"):
            self._fix_ram_images()

    def _fix_ram_images(self):
        rw = self.device.internal.rwdata
        for r in self.map.refs:
            kind, src = r["kind"], r["src"]
            if kind in ("ITCM literal", "DTCM .data"):
                for data, dst in zip(rw.datas, rw.dsts):
                    if dst <= src < dst + len(data):
                        self._fix_abs(data, src - dst, kind)
                        break
                else:
                    # Zelda copies its ITCM code out of external flash at boot.
                    ext = self._copy_source(src)
                    buf, off = (None, None) if ext is None else self.device.locate(ext)
                    if buf is None:
                        raise NotImplementedError(
                            f"{kind} at 0x{src:08X} isn't in an rwdata block"
                        )
                    self._fix_abs(buf, off, kind)
            elif kind in ("external literal", "external data pointer"):
                buf, off = self.device.locate(src)
                if buf is None:
                    raise NotImplementedError(f"can't locate {kind} at 0x{src:08X}")
                self._fix_abs(buf, off, kind)

    def _copy_source(self, addr):
        """External address that a boot-time ITCM/DTCM copy fills ``addr`` from."""
        layout = xrefs.LAYOUT[self.device.name]
        for src, dst, n in layout.get("itcm_copies", []) + layout.get(
            "dtcm_copies", []
        ):
            if dst <= addr < dst + n:
                return src + addr - dst
        return None

    def _elf_relocs(self):
        """(address, type, symbol name) of every relocation in our ELF that
        lands in flash."""
        elf = self.device.internal.elf
        load = [
            (s["p_vaddr"], s["p_paddr"], s["p_memsz"])
            for s in elf.iter_segments()
            if s["p_type"] == "PT_LOAD"
        ]
        for sec in elf.iter_sections():
            if not isinstance(sec, RelocationSection):
                continue
            target = elf.get_section(sec["sh_info"])
            if not target["sh_flags"] & 2:  # SHF_ALLOC
                continue
            symtab = elf.get_section(sec["sh_link"])
            for rel in sec.iter_relocations():
                vaddr = rel["r_offset"]
                name = symtab.get_symbol(rel["r_info_sym"]).name
                for v, p, n in load:  # .data relocations land in its flash copy
                    if v <= vaddr < v + n:
                        yield p + vaddr - v, rel["r_info_type"], name
                        break

    def apply(self):
        d = self.device
        base = d.internal.FLASH_BASE
        cur = bytes(d.internal)
        lo, hi, delta = self.lo, self.hi, self.delta
        moved = bytearray(cur[lo - base : hi - base])
        prov = writelog.provenance(d.internal, lo - base, hi - base)

        def where(addr):
            if self.moves(addr):
                return moved, addr - lo
            return d.internal, addr - base

        # Current code in the range (patches changed some of it).
        code = Code(cur, base, lo + xrefs.VECTOR_TABLE_END, hi)
        # Freed stock code may hold data now. Live code can still branch there
        # behind a guard that's never taken (e.g. sleep_image_tick skips the
        # removed decoder when the images are deleted), so don't descend into it.
        code.contains = lambda a: code.lo <= a < code.hi and not self.freed(a)
        with contextlib.redirect_stdout(io.StringIO()):
            code.descend({a | 1 for a in self.map.code.entries if self.moves(a)})

        # Branches out of the range (e.g. hooks into our code).
        for src, t, mn, size in code.branches:
            if self.moves(t):
                continue
            if size != 4:
                raise NotImplementedError(
                    f"16-bit branch at 0x{src:08X} leaves the range"
                )
            o = src - lo
            hw1, hw2 = struct.unpack_from("<HH", moved, o)
            hw1, hw2 = _encode_branch(hw1, hw2, t - (src + delta + 4))
            struct.pack_into("<HH", moved, o, hw1, hw2)
            self._count("branch out")
        for src, t, sz in code.pcrel:
            if not self.moves(t):
                raise NotImplementedError(
                    f"PC-relative access at 0x{src:08X} leaves the range"
                )

        # Our code: branches into and pointers to the range. The amalgam's
        # ELF also calls the other firmware's stock code at the same addresses.
        stock_end = base + d.internal.STOCK_ROM_END
        for addr, rtype, name in self._elf_relocs():
            if not stock_end <= addr < base + len(d.internal):
                continue
            if name.startswith("stock_") and not name.startswith(f"stock_{d.name}_"):
                continue
            o = addr - base
            if rtype in (R_ARM_THM_CALL, R_ARM_THM_JUMP24):
                hw1, hw2 = struct.unpack_from("<HH", d.internal, o)
                decoded = _decode_branch(hw1, hw2)
                if decoded is None:
                    continue  # ld turns calls to undefined weak symbols into nop.w
                kind, off = decoded
                t = addr + 4 + off
                if self.moves(t):
                    hw1, hw2 = _encode_branch(hw1, hw2, t + delta - (addr + 4))
                    _pack("<HH", d.internal, o, hw1, hw2)
                    self._count("our branch in")
            elif rtype == R_ARM_ABS32:
                self._fix_abs(d.internal, o, "our pointer")
        for o in d.int_pointers:
            self._fix_abs(d.internal, o, "patcher pointer")

        # Absolute words in internal flash.
        absolute = {
            r["src"]
            for r in self.map.refs
            if r["kind"] in ("vector", "literal", "pointer in flash data")
        }
        absolute |= {a for a, sz in code.literals().items() if sz == 4}
        pc_offsets = {
            r["src"]: r["base"]
            for r in self.map.refs
            if r["kind"] == "pc-offset literal"
        }
        absolute = {a for a in absolute if not self.freed(a)}
        for a in sorted(absolute - set(pc_offsets)):
            buf, o = where(a)
            self._fix_abs(buf, o, "absolute")

        # Self-relative words; the RWDATA table is the patcher's, read it as written.
        rw = d.internal.rwdata
        selfrel = {
            r["src"]: r["src"] for r in self.map.refs if "self-relative" in r["kind"]
        }
        table = base + rw.table_start
        selfrel = {a: a for a in selfrel if not table <= a < base + rw.table_end}
        for i in range(len(rw.datas)):
            for f in (0, 4):
                selfrel[table + 16 * i + f] = table + 16 * i + f
        for a in (base + rw.table_end - 8, base + rw.table_end - 4):
            selfrel[a] = a
        selfrel.update(pc_offsets)
        selfrel = {a: b for a, b in selfrel.items() if not self.freed(a)}
        for a, b in sorted(selfrel.items()):
            v = struct.unpack_from("<i", cur, a - base)[0]
            t = (b + v) & 0xFFFF_FFFF
            new_t = t + delta if self.moves(t & ~1) else t
            new_b = b + delta if self.moves(b) else b
            if new_t - new_b != v:
                buf, o = where(a)
                _pack("<i", buf, o, new_t - new_b)
                self._count("self-relative")

        # The bytes changed in the moved copy are this fix-up's, unless a
        # patch wrote them (e.g. a hook's bl, re-encoded).
        before = np.frombuffer(cur[lo - base : hi - base], dtype=np.uint8)
        changed = np.frombuffer(bytes(moved), dtype=np.uint8) != before
        prov[changed & (prov < 0)] = writelog.new()

        vt = xrefs.VECTOR_TABLE_END
        if lo == base and self.keep_vector_table:
            with writelog.untracked():
                d.internal[:vt] = moved[:vt]
            writelog.set_provenance(d.internal, 0, prov[:vt])

        if dst_in_bank2(self.dst):
            off = self.dst - BANK2_BASE
            out = d.bank2
            if len(out) < off + len(moved):
                out.extend(b"\xff" * (off + len(moved) - len(out)))
        else:
            off = self.dst - base
            out = d.internal
            # Freed stock code moved with the rest, but its holes were handed
            # out at the new place (Device.free_int) and may hold data now.
            for start, end in d.freed_int:
                if self.moves(base + start):
                    s, e = start - (lo - base), end - (lo - base)
                    moved[s:e] = out[off + s : off + e]
                    prov[s:e] = writelog.provenance(out, off + s, off + e)
        with writelog.untracked():
            out[off : off + len(moved)] = moved
        if out is d.internal:
            writelog.set_provenance(out, off, prov)
        self.prov = prov

        # Old copy: udf, so anything still pointing here faults. The OTFDEC
        # key/nonce stay; the patcher encrypts external flash with them.
        start = max(lo, base + vt)
        keep = (base + d.internal.NONCE_OFFSET, base + d.internal.KEY_OFFSET + 16)
        with writelog.untracked():
            for a in range(start, hi, 2):
                if not keep[0] <= a < keep[1]:
                    d.internal[a - base : a - base + 2] = b"\xfe\xde"
        return self.counts


def _pack(fmt, buf, off, *values):
    """``struct.pack_into``, logged for the manifest (it bypasses
    ``Firmware.__setitem__``)."""
    struct.pack_into(fmt, buf, off, *values)
    if getattr(buf, "prov", None) is not None:
        writelog.record(buf, off, off + struct.calcsize(fmt), fixup=True)


def dst_in_bank2(addr):
    return BANK2_BASE <= addr < BANK2_END
