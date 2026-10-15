#!/usr/bin/env python3
"""Print the map of stock internal code and every reference into it.

The analysis lives in ``patches/xrefs.py`` (the patcher uses it to relocate
stock code); this prints byte classification, the relocatable units (armcc
shares literal pools across a module, so some functions must move together),
branches between units, and every absolute or self-relative reference into
internal code.

Usage:
    python tools/re/xrefs.py --device=mario [--units] [--json build/re/mario_xrefs.json]
"""

import argparse
import bisect
import json
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from patches.xrefs import LAYOUT, analyze, units  # noqa: E402


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--device", required=True, choices=sorted(LAYOUT))
    parser.add_argument(
        "--units", action="store_true", help="List every relocatable unit."
    )
    parser.add_argument(
        "--json",
        type=Path,
        help="Write units and references here (keep it under build/).",
    )
    args = parser.parse_args()

    a = analyze(args.device)
    code, cls, refs, other, unreferenced = (
        a.code,
        a.cls,
        a.refs,
        a.other,
        a.unreferenced,
    )
    c = Counter(cls)
    print(
        f"internal 0x{code.lo:08X}-0x{code.hi:08X}: code {c[1]} B, literals {c[2]} B, "
        f"jump tables {c[3]} B, other {c[0]} B; {len(code.entries)} entries"
    )
    if code.unbounded_tables:
        print(
            "  tbb/tbh without a bound (targets not followed):",
            [hex(a) for a in code.unbounded_tables],
        )
    for name, o in other.items():
        print(f"{name} code 0x{o.lo:08X}-0x{o.hi:08X}: {len(o.insns)} instructions")

    spans = units(code)
    sizes = sorted((hi - lo for lo, hi in spans), reverse=True)
    print(
        f"\n{len(spans)} relocatable units: largest {sizes[0]} B, median {sizes[len(sizes) // 2]} B, "
        f"{sum(s > 1024 for s in sizes)} over 1 KB"
    )
    unit_lo = [lo for lo, _ in spans]

    def unit(a):
        i = bisect.bisect_right(unit_lo, a) - 1
        return i if i >= 0 and a < spans[i][1] else None

    cross = Counter()
    for src, t, mn, size in code.branches:
        if code.contains(t) and unit(src) != unit(t):
            cross[f"{mn} ({size} B)"] += 1
    print("branches between units (fix by re-encoding):", dict(cross))
    narrow = [k for k in cross if k.endswith("(2 B)")]
    if narrow:
        print("  WARNING: 16-bit branches between units:", narrow)

    print("\nabsolute / self-relative references into internal code:")
    for kind, n in Counter(r["kind"] for r in refs).most_common():
        print(f"  {kind:32} {n}")
    print(
        f"\nunreferenced push prologues in non-code bytes (dead, or reached some way not modeled): "
        f"{len(unreferenced)}",
        [hex(a) for a in unreferenced],
    )
    if args.units:
        print()
        for lo, hi in spans:
            print(f"  0x{lo:08X}-0x{hi:08X} {hi - lo:6} B")
    if args.json:
        args.json.write_text(
            json.dumps(
                {"units": spans, "refs": refs, "unreferenced": unreferenced}, indent=1
            )
        )
        print(f"\nWrote {args.json}")


if __name__ == "__main__":
    main()
