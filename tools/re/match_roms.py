#!/usr/bin/env python3
"""List the stock functions that are identical in the Mario and Zelda firmware.

Both firmwares are built from the same Nintendo SDK (HAL, C runtime, power and
NVRAM management, flash driver, draw library, NES core), so a large part of the
code is byte-for-byte the same apart from addresses. This finds those pairs:

1. Function bodies come from the recursive-descent map in ``patches/xrefs.py``
   (internal flash, ITCM after boot, external-flash code), split at every entry
   point (seeds and ``bl`` targets).
2. Each body becomes a signature with addresses masked: branch targets, and
   literal-pool words unless they look like constants (peripheral addresses,
   small numbers).
3. Signatures that occur exactly once in each firmware pair up. Then, for
   paired functions with the same number of calls, the callees pair up in call
   order, repeated until nothing new turns up.

Bodies shorter than ``--min-insns`` are left out: tiny thunks match by accident.

Usage:
    python tools/re/match_roms.py [--json build/re/rom_match.json] [--names]
"""

import argparse
import hashlib
import json
import re
import struct
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from patches.xrefs import analyze, parse_syms  # noqa: E402

DEVICES = ("mario", "zelda")


def is_constant(v):
    """Literal words kept in signatures: small numbers and peripheral/system addresses."""
    return v < 0x1_0000 or 0x4000_0000 <= v < 0x6000_0000 or v >= 0xE000_0000


def entries(code, syms=()):
    out = set(code.entries)
    out |= {t for _, t, mn, _ in code.branches if mn.startswith("bl")}
    out |= {a for a in syms if a in code.insns}
    return {a for a in out if a in code.insns}


def _in_it_block(code, a):
    """True if the instruction at ``a`` is conditional through a preceding IT."""
    p = a
    for k in range(1, 5):
        prev = next(
            (p - s for s in (2, 4) if code.insns.get(p - s, (0,))[0] == s), None
        )
        if prev is None:
            return False
        p = prev
        mn = code.insns[p][1]
        if mn.startswith("it"):
            return len(mn) - 1 >= k
    return False


def bodies(code, starts, stop):
    """{entry: sorted instruction addresses} reachable without crossing another
    entry in ``stop`` (a set of all entry points, so tail calls end a body)."""
    branch = {src: (t, mn) for src, t, mn, _ in code.branches}
    tables = {src: (s, e) for src, s, e in code.tables}
    out = {}
    for e in starts:
        seen, work = set(), [e]
        while work:
            a = work.pop()
            if a in seen or a not in code.insns or (a != e and a in stop):
                continue
            seen.add(a)
            size, mn, op = code.insns[a]
            m = mn.split(".")[0]
            nxt = a + size
            if a in branch:
                t, bmn = branch[a]
                if not bmn.startswith("bl"):
                    work.append(t)
                if bmn.split(".")[0] != "b":
                    work.append(nxt)
                continue
            if a in tables:
                s, end = tables[a]
                esz = 1 if m == "tbb" else 2
                for i in range(s, end, esz):
                    o = i - code.base
                    off = (
                        code.mem[o]
                        if esz == 1
                        else struct.unpack_from("<H", code.mem, o)[0]
                    )
                    work.append(s + 2 * off)
                continue
            returns = m in ("bx", "udf") or (
                "pc" in op and (m.startswith(("pop", "ldm")) or op.startswith("pc,"))
            )
            if not returns or _in_it_block(code, a):
                work.append(nxt)
        out[e] = sorted(seen)
    return out


def signature(code, addrs):
    """(signature tokens, callee addresses in order) of one body."""
    branch = {src: (t, mn) for src, t, mn, _ in code.branches}
    pcrel = {src: (lit, size) for src, lit, size in code.pcrel}
    toks, calls = [], []
    for a in addrs:
        _, mn, op = code.insns[a]
        if a in branch:
            t, bmn = branch[a]
            if bmn.startswith("bl"):
                toks.append("bl")
                calls.append(t)
            else:
                toks.append(mn + " " + re.sub(r"#-?0x[0-9a-f]+", "T", op))
        elif a in pcrel and pcrel[a][1] == 4:
            o = pcrel[a][0] - code.base
            v = (
                struct.unpack_from("<I", code.mem, o)[0]
                if 0 <= o <= len(code.mem) - 4
                else None
            )
            value = f"{v:#x}" if v is not None and is_constant(v) else "ADDR"
            toks.append(f"{mn} {op.split(',')[0]} ={value}")
        elif a in pcrel:
            toks.append(f"{mn} {op.split(',')[0]} =L")
        else:
            toks.append(f"{mn} {op}")
    return toks, calls


class Rom:
    """Function signatures of one firmware."""

    def __init__(self, regions, syms=()):
        all_entries = {}
        for name, code in regions.items():
            all_entries[name] = entries(code, syms)
        stop = set().union(*all_entries.values())
        self.sig, self.calls, self.size, self.region = {}, {}, {}, {}
        for name, code in regions.items():
            for e, addrs in bodies(code, sorted(all_entries[name]), stop).items():
                self.sig[e], self.calls[e] = signature(code, addrs)
                self.size[e] = sum(code.insns[a][0] for a in addrs)
                self.region[e] = name

    @classmethod
    def from_device(cls, device):
        a = analyze(device)
        regions = {
            "internal": a.code,
            "itcm": a.other["ITCM"],
            "external": a.other["external"],
        }
        funcs, _ = parse_syms(REPO / "tools" / "re" / f"{device}.syms")
        return cls(regions, funcs)


def _key(toks):
    return hashlib.sha1("\n".join(toks).encode()).hexdigest()


def match(a, b, min_insns=6):
    """{a_entry: b_entry} of identical functions."""

    def big(rom, e):
        return len(rom.sig[e]) >= min_insns

    groups = (defaultdict(list), defaultdict(list))
    for rom, g in zip((a, b), groups):
        for e, toks in rom.sig.items():
            if big(rom, e):
                g[_key(toks)].append(e)
    pairs = {
        ga[0]: groups[1][k][0]
        for k, ga in groups[0].items()
        if len(ga) == 1 and len(groups[1].get(k, ())) == 1
    }
    taken = set(pairs.values())
    changed = True
    while changed:
        changed = False
        for x, y in list(pairs.items()):
            cx, cy = a.calls[x], b.calls[y]
            if len(cx) != len(cy):
                continue
            for p, q in zip(cx, cy):
                if p in pairs or q in taken or p not in a.sig or q not in b.sig:
                    continue
                if big(a, p) and big(b, q) and a.sig[p] == b.sig[q]:
                    pairs[p] = q
                    taken.add(q)
                    changed = True
    return pairs


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--min-insns", type=int, default=6)
    parser.add_argument(
        "--json", type=Path, help="Write the pairs here (keep it under build/)."
    )
    parser.add_argument(
        "--names", action="store_true", help="List every pair with its .syms names."
    )
    args = parser.parse_args()

    roms = {d: Rom.from_device(d) for d in DEVICES}
    mario, zelda = roms["mario"], roms["zelda"]
    pairs = match(mario, zelda, args.min_insns)
    names = {d: parse_syms(REPO / "tools" / "re" / f"{d}.syms")[0] for d in DEVICES}

    by_region = defaultdict(lambda: [0, 0])
    for m, z in pairs.items():
        r = by_region[(mario.region[m], zelda.region[z])]
        r[0] += 1
        r[1] += mario.size[m]
    total = sum(mario.size[m] for m in pairs)
    print(
        f"{len(pairs)} identical functions ({total} B of instructions in each firmware)"
    )
    for (rm, rz), (n, size) in sorted(by_region.items(), key=lambda kv: -kv[1][1]):
        print(f"  mario {rm:8s} <-> zelda {rz:8s}: {n:4d} functions, {size:6d} B")

    mismatched = [
        (m, z)
        for m, z in pairs.items()
        if m in names["mario"]
        and z in names["zelda"]
        and names["mario"][m] != names["zelda"][z]
    ]
    unnamed = sum(
        1
        for m, z in pairs.items()
        if m not in names["mario"] and z not in names["zelda"]
    )
    one_sided = (
        len(pairs)
        - unnamed
        - sum(
            1 for m, z in pairs.items() if m in names["mario"] and z in names["zelda"]
        )
    )
    print(
        f"names: {unnamed} pairs unnamed in both, {one_sided} named on one side only, "
        f"{len(mismatched)} with different names"
    )
    for m, z in sorted(mismatched):
        print(f"  0x{m:08x} {names['mario'][m]}  vs  0x{z:08x} {names['zelda'][z]}")

    if args.names:
        for m, z in sorted(pairs.items()):
            print(
                f"0x{m:08x} 0x{z:08x} {mario.size[m]:5d} B  "
                f"{names['mario'].get(m, '-')}  {names['zelda'].get(z, '-')}"
            )
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(
            json.dumps(
                [
                    {
                        "mario": m,
                        "zelda": z,
                        "size": mario.size[m],
                        "mario_region": mario.region[m],
                        "zelda_region": zelda.region[z],
                        "mario_name": names["mario"].get(m),
                        "zelda_name": names["zelda"].get(z),
                    }
                    for m, z in sorted(pairs.items())
                ],
                indent=1,
            )
        )
        print(f"Wrote {args.json}")


if __name__ == "__main__":
    main()
