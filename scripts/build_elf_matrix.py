"""Build our code for every combination of the patch options the C build
depends on (``patches/prebuilt.py``), for patching without a compiler.

    python -m scripts.build_elf_matrix build/prebuilt [--only NAME ...]

Each ELF is built by the Makefile in its own copy of the files the C build
reads (``BUILD_FILES``) and written to ``<out>/<name>.elf``, with ``<out>/manifest.json``
(``prebuilt.write_manifest``); ``patch.py --prebuilt <out>`` picks one. No
stock backups are needed.
"""

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from patches import prebuilt

REPO = Path(__file__).resolve().parents[1]


# What `make build/gw_patch.elf` reads (pathspecs).
BUILD_FILES = ["Core", "Drivers", "Makefile*", "*.ld", "scripts", "patches", "tools/re"]


def copy_tree(dst, pathspecs=()):
    """The working tree's tracked and untracked (not ignored) files, minus
    build/; only those matching ``pathspecs``, if given."""
    files = subprocess.run(
        ["git", "ls-files", "-co", "--exclude-standard", "-z", "--", *pathspecs],
        cwd=REPO,
        capture_output=True,
        check=True,
    ).stdout.split(b"\0")
    for rel in files:
        rel = rel.decode()
        if not rel or rel.startswith(("build/", ".claude/")):
            continue
        if not (REPO / rel).is_file():
            continue
        (dst / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO / rel, dst / rel)


def build(name, params, out, python=sys.executable):
    """Build one ELF; returns the make output on failure, else None."""
    with tempfile.TemporaryDirectory() as tmp:
        tree = Path(tmp)
        copy_tree(tree, BUILD_FILES)
        proc = subprocess.run(
            [
                "make",
                f"PYTHON={python}",
                f"PATCH_PARAMS={params}",
                "build/gw_patch.elf",
            ],
            cwd=tree,
            capture_output=True,
            text=True,
        )
        if proc.returncode:
            return proc.stdout[-4000:] + proc.stderr[-4000:]
        shutil.copy2(tree / "build" / "gw_patch.elf", out / f"{name}.elf")
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("out", type=Path)
    parser.add_argument("--only", nargs="*", help="Build only these names.")
    parser.add_argument("-j", "--jobs", type=int, default=os.cpu_count())
    args = parser.parse_args()

    combos = prebuilt.combos()
    if args.only:
        unknown = set(args.only) - set(combos)
        if unknown:
            parser.error(f"unknown names: {' '.join(sorted(unknown))}")
        combos = {n: combos[n] for n in args.only}
    args.out.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(args.jobs) as pool:
        futures = {
            n: pool.submit(build, n, p, args.out) for n, p in sorted(combos.items())
        }
    failed = {n: f.result() for n, f in futures.items() if f.result()}
    for n, log in failed.items():
        print(f"=== {n} ({combos[n]})\n{log}", file=sys.stderr)
    if not failed:
        prebuilt.write_manifest(args.out, combos)
    print(f"Built {len(combos) - len(failed)} of {len(combos)} ELFs in {args.out}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
