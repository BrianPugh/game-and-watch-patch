"""Run a unit of stock internal code from external flash (execute in place).

A unit (``xrefs.units``) moves whole: the code reaches outside it only through
32-bit ``bl``/``b.w``, and nothing outside reads it. At each entry point the
internal copy keeps a 4-byte ``ldr.w pc, [pc, #k]`` stub into the external
copy (a shared literal pool follows the last stub), so no caller changes. The
external copy reaches internal code through 8-byte ``ldr.w pc, [pc]`` veneers
appended to it. The rest of the unit is freed (``Device.free_int``).

External flash isn't mapped during boot (static constructors run first) or
while the firmware writes it, so ``check`` rejects a unit that any of these
can reach: an interrupt handler, the static-constructor and init tables, the
calls ``main`` makes before ``power_on_init``, and the flash-write and sleep
windows (``WINDOWS`` and their callers). A static constructor inside the unit
stays internal (``kept``), with the code and literals it reaches.

``XipUnit`` runs in 2 steps: the constructor frees the internal space before
data is moved there, and ``write`` puts the copy in external flash once its
pointers are final (after ``fix_ext_refs``, which fixes the copy's literals
through ``Device._ref_site``).
"""

import bisect
import struct
from collections import defaultdict

import numpy as np

from . import writelog
from .relocate import _decode_branch, _encode_branch, elf_relocs
from .utils import round_up_word
from .xrefs import Code, units

# Functions that turn external flash off: flash writes switch memory-mapped
# mode off, and sleep powers the OSPI down.
WINDOWS = ["flash_disable_memory_mapped", "peripherals_deinit", "resume_peripherals"]

R_ARM_THM_CALL = 10
R_ARM_THM_JUMP24 = 30


def _stub(literal_offset):
    """``ldr.w pc, [pc, #literal_offset]``."""
    return struct.pack("<HH", 0xF8DF, 0xF000 | literal_offset)


VENEER = struct.pack("<HH", 0xF8DF, 0xF000)  # ldr.w pc, [pc, #0]; then the target


class XipError(ValueError):
    pass


def _self_relative(kind):
    return "self-relative" in kind or kind in ("ctor", "init table", "rwdata table")


def check(analysis, syms, lo, hi):
    """The unit's entry points and constructor entries, after checking it can
    run from external flash. ``analysis``: ``xrefs.Analysis`` of the stock
    firmware; ``syms``: its ``.syms`` names."""
    code = analysis.code
    spans = units(code)
    starts = [s for s, _ in spans]

    def unit_of(a):
        i = bisect.bisect_right(starts, a & ~1) - 1
        return i if i >= 0 and spans[i][0] <= a & ~1 < spans[i][1] else None

    me = unit_of(lo)
    if me is None or spans[me] != [lo, hi]:
        raise XipError(f"0x{lo:08X}-0x{hi:08X} isn't a unit of stock code")

    def inside(a):
        return lo <= a & ~1 < hi

    entries, ctors = set(), set()
    for src, t, mn, size in code.branches:
        if inside(src) == inside(t):
            continue
        if size != 4:
            raise XipError(f"{mn} at 0x{src:08X} crosses the unit boundary")
        if inside(t):
            entries.add(t & ~1)
    for src, t, _ in code.pcrel:
        if inside(src) != inside(t):
            raise XipError(f"PC-relative access at 0x{src:08X} crosses the unit")
    for r in analysis.refs:
        src, t, kind = r["src"], r["target"], r["kind"]
        if inside(src) and kind != "literal":
            raise XipError(f"{kind} at 0x{src:08X} inside the unit")
        if not inside(t) or inside(src):
            continue
        if kind == "ctor (self-relative)":
            ctors.add(t & ~1)
        else:
            raise XipError(f"{kind} at 0x{src:08X} points into the unit (0x{t:08X})")

    # Units external flash must not be needed by, and everything they reach.
    edges = defaultdict(set)
    for src, t, _, _ in code.branches:
        s, d = unit_of(src), unit_of(t)
        if s is not None and d is not None and s != d:
            edges[s].add(d)
    roots, nofollow = {}, set()
    for r in analysis.refs:
        d = unit_of(r["target"])
        if d is None:
            continue
        s = unit_of(r["src"]) if code.contains(r["src"]) else None
        if r["kind"] == "vector":
            roots.setdefault(d, f"vector 0x{r['src']:08X}")
        elif _self_relative(r["kind"]):
            if d != me:  # the unit's own constructors stay internal
                roots.setdefault(d, f"{r['kind']} 0x{r['src']:08X}")
        elif s is not None and s != d:
            edges[s].add(d)
    reset = struct.unpack_from("<I", analysis.internal, 4)[0]
    nofollow = {unit_of(reset), unit_of(syms["main"])}
    main, power_on_init = syms["main"] & ~1, syms["power_on_init"] & ~1
    for src, t, mn, _ in sorted(code.branches):
        if src < main or not mn.startswith("bl"):
            continue
        if t & ~1 == power_on_init:
            break
        roots.setdefault(unit_of(t), f"main before power_on_init (0x{src:08X})")
    callers = defaultdict(set)
    for s, ds in edges.items():
        for d in ds:
            callers[d].add(s)
    for name in WINDOWS:
        u = unit_of(syms[name])
        roots.setdefault(u, name)
        for c in callers[u]:
            roots.setdefault(c, f"calls {name}")
    roots.pop(None, None)
    why = dict(roots)
    work = [u for u in roots if u not in nofollow]
    while work:
        u = work.pop()
        for d in edges.get(u, ()):
            if d not in why:
                why[d] = why[u]
                work.append(d)
    if me in why:
        raise XipError(f"0x{lo:08X}-0x{hi:08X} is reachable from {why[me]}")
    return sorted(entries), sorted(ctors)


class XipUnit:
    """Stock internal ``[lo, hi)`` of ``device``, moved to external flash."""

    def __init__(self, device, lo, hi, label):
        d = self.device = device
        self.lo, self.hi, self.label = lo, hi, label
        base = d.internal.FLASH_BASE
        entries, ctors = check(d.stock_map, d.internal.syms, lo, hi)
        # Our code may call into the unit too (stock_<device>_* symbols).
        for addr, rtype, name in elf_relocs(d):
            if rtype not in (R_ARM_THM_CALL, R_ARM_THM_JUMP24):
                continue
            if not name.startswith(f"stock_{d.name}_"):
                continue
            hw1, hw2 = struct.unpack_from("<HH", d.internal, addr - base)
            decoded = _decode_branch(hw1, hw2)
            if decoded and lo <= addr + 4 + decoded[1] < hi:
                entries.append(addr + 4 + decoded[1])
        self.entries = sorted(set(entries))
        self.ctors = ctors

        # The unit as patched so far; fix_ext_refs() fixes its literals here.
        self.buf = bytearray(d.internal[lo - base : hi - base])
        self.orig = bytes(self.buf)
        self.prov = writelog.provenance(d.internal, lo - base, hi - base)
        self.code = self._descend()

        # What stays internal: the constructors, then a stub per entry.
        kept = np.zeros(hi - lo, dtype=bool)
        if ctors:
            ctor_code = Code(bytes(self.buf), lo, lo, hi)
            ctor_code.descend({a | 1 for a in ctors})
            for a, (sz, _, _) in ctor_code.insns.items():
                kept[a - lo : a - lo + sz] = True
            for lit, sz in ctor_code.literals().items():
                kept[lit - lo : lit - lo + sz] = True
            for src, t, mn, _ in ctor_code.branches:
                if not lo <= t < hi:
                    raise XipError(f"constructor code at 0x{src:08X} leaves the unit")
        self.kept = kept.copy()
        stubs = {}
        for e in self.entries:
            if kept[e - lo : e - lo + 4].any() or e + 4 > hi:
                raise XipError(f"no room for a stub at entry 0x{e:08X}")
            kept[e - lo : e - lo + 4] = True
            stubs[e] = None
        pool = round_up_word(max(self.entries, default=lo) + 4)
        while kept[pool - lo : pool - lo + 4 * len(stubs)].any():
            pool += 4
        for i, e in enumerate(stubs):
            stubs[e] = pool + 4 * i
            offset = stubs[e] - ((e + 4) & ~3)
            if not 0 <= offset < 0x1000:
                raise XipError(f"stub at 0x{e:08X} can't reach its literal")
        if pool + 4 * len(stubs) > hi:
            raise XipError("no room for the stubs' literals")
        kept[pool - lo : pool - lo + 4 * len(stubs)] = True
        self.stubs = stubs
        self.pool = pool

        # Free everything else, then write the stubs (literals by write()).
        free = np.flatnonzero(np.diff(np.concatenate(([1], kept, [1])).astype(int)))
        for s, e in zip(free[::2], free[1::2]):
            s, e = lo - base + int(s), lo - base + int(e)
            d.internal[s:e] = b"\xfe\xde" * ((e - s) // 2)  # udf, like free_int
            if e - s >= 8:
                d.free_int(s, e)
        for e, lit in stubs.items():
            d.internal[e - base : e - base + 4] = _stub(lit - ((e + 4) & ~3))
            d.internal[lit - base : lit - base + 4] = b"\x00" * 4
        self.ext_addr = None
        d.xip_units.append(self)

    def _descend(self):
        """The unit's code from every entry (stock's and ours)."""
        lo, hi = self.lo, self.hi
        seeds = {a | 1 for a in self.device.stock_map.code.entries if lo <= a < hi}
        code = Code(bytes(self.buf), lo, lo, hi)
        code.descend(seeds | {a | 1 for a in self.entries})
        return code

    def site(self, src):
        """(buffer, offset) of the unit's word at stock ``src``, or None."""
        if self.lo <= src < self.hi and not self.kept[src - self.lo]:
            return self.buf, src - self.lo
        return None

    def hides(self, addr):
        """Whether internal ``addr`` holds no code of the unit any more."""
        if not self.lo <= addr < self.hi:
            return False
        return not self.kept[addr - self.lo] and addr not in self.stubs

    def _veneer_targets(self):
        lo, hi = self.lo, self.hi
        return sorted({t for _, t, _, _ in self.code.branches if not lo <= t < hi})

    @property
    def ext_size(self):
        """Bytes the external copy takes."""
        return round_up_word(self.hi - self.lo) + 8 * len(self._veneer_targets())

    def write(self, offset):
        """Put the unit at external ``offset`` (the device's buffer, word
        aligned like the unit) and point the stubs at it."""
        d = self.device
        lo, hi = self.lo, self.hi
        ext = d.external.FLASH_BASE + offset
        if (ext - lo) % 4:
            raise XipError("the external copy must keep the unit's word alignment")
        # Patches after the snapshot (hooks into the unit) would be lost.
        code = self._descend()
        if code.branches != self.code.branches:
            raise XipError("the unit's branches changed after it was moved")
        r = d.relocation

        def final(a):
            return a + r.delta if r and r.moves(a & ~1) else a

        out = bytearray(self.buf).ljust(round_up_word(hi - lo), b"\x00")
        targets = self._veneer_targets()
        veneer = {t: len(out) + 8 * i for i, t in enumerate(targets)}
        for t in targets:
            out += VENEER + struct.pack("<I", final(t) | 1)
        for src, t, mn, _ in code.branches:
            if lo <= t < hi:
                continue
            o = src - lo
            hw1, hw2 = struct.unpack_from("<HH", out, o)
            hw1, hw2 = _encode_branch(hw1, hw2, veneer[t] - (o + 4))
            struct.pack_into("<HH", out, o, hw1, hw2)
        for lit, sz in code.literals().items():
            if sz != 4:
                continue
            v = struct.unpack_from("<I", out, lit - lo)[0]
            if lo <= v & ~1 < hi:
                v += ext - lo
            else:
                v = final(v)
            struct.pack_into("<I", out, lit - lo, v)

        # Bytes changed since the unit was taken: fix_ext_refs() and the above.
        prov = np.concatenate([self.prov, np.full(len(out) - len(self.prov), -1)])
        changed = np.frombuffer(bytes(out[: hi - lo]), np.uint8) != np.frombuffer(
            self.orig, np.uint8
        )
        step = writelog.new(f"{self.label}: veneers and pointer fixups")
        prov[: hi - lo][changed] = step
        prov[hi - lo :] = step
        with writelog.untracked():
            d.external[offset : offset + len(out)] = out
        writelog.set_provenance(d.external, offset, prov)
        self.ext_addr = ext
        self.ext_prov = prov[: hi - lo]

        base = d.internal.FLASH_BASE
        for e, lit in self.stubs.items():
            d.internal[lit - base : lit - base + 4] = struct.pack(
                "<I", (e + ext - lo) | 1
            )
        d.ext_space.note(offset, len(out), d.name, self.label)
        return len(out)

    def final_address(self, addr):
        """Where stock ``addr`` in the unit runs now."""
        if self.ext_addr is None:
            return addr
        if self.kept[addr - self.lo] and addr not in self.stubs:
            return addr
        return addr - self.lo + self.ext_addr
