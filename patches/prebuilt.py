"""Prebuilt copies of our code, for patching without a compiler.

The C build (``Makefile``) depends on a few patch options. ``combos()`` lists
every combination the patcher accepts, ``scripts/build_elf_matrix.py`` builds
one ELF per combination, and ``name()`` picks the ELF a command line needs
(``patch.py --prebuilt <dir>``). The rules mirror the Makefile's tests of
``PATCH_PARAMS``, which match by substring (``findstring``), except the sleep
inflate functions, which it matches by whole word (``filter``).
"""

import argparse
import itertools

from elftools.elf.constants import SH_FLAGS
from elftools.elf.elffile import ELFFile

# Options the Makefile tests with findstring: (name part, option).
FLAGS = [
    ("sd", "--sd-bootloader"),
    ("triple", "--triple-boot"),
    ("clock", "--clock-only"),
    ("smb1", "--smb1-graphics"),
    ("gsdb", "--game-select-dual-boot"),
]
# Any of these leaves sleep_image_inflate/backdrop_inflate unlinked.
NO_SLEEP = ("--no-sleep-images", "--slim", "--internal-only", "--clock-only")
# Flags each device's parser rejects (Zelda's has no Mario-only options; the
# amalgam rejects --triple-boot).
UNSUPPORTED = {
    "mario": set(),
    "zelda": {"clock", "smb1"},
    "amalgam": {"triple"},
}


class PrebuiltError(Exception):
    pass


def _name(device, flags, sleep):
    parts = [device, *(n for n, _ in FLAGS if n in flags)]
    if not sleep:
        parts.append("nosleep")
    return "-".join(parts)


def combos():
    """``{name: PATCH_PARAMS}`` of every ELF the patcher can need."""
    out = {}
    for device, unsupported in UNSUPPORTED.items():
        names = [n for n, _ in FLAGS if n not in unsupported]
        for picked in itertools.product((False, True), repeat=len(names)):
            flags = {n for n, p in zip(names, picked) if p}
            for sleep in (True, False):
                if sleep and "clock" in flags:
                    continue  # --clock-only drops the sleep images
                params = [f"--device={device}"]
                params += [opt for n, opt in FLAGS if n in flags]
                if not sleep and "clock" not in flags:
                    params.append("--no-sleep-images")
                out[_name(device, flags, sleep)] = " ".join(params)
    return out


def name(argv):
    """Name of the prebuilt ELF for the patch command line ``argv``."""
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--device", default="mario")
    args, _ = parser.parse_known_args(argv)
    params = " ".join(argv)
    if "--debug" in params:
        raise PrebuiltError("--debug needs a build from source")
    flags = {n for n, opt in FLAGS if opt in params}
    sleep = not set(NO_SLEEP) & set(params.split())
    out = _name(args.device, flags, sleep)
    if out not in combos():
        raise PrebuiltError(f"no prebuilt code for {params!r}")
    return out


def elf_to_bin(data):
    """``objcopy -O binary`` of an ELF: its loaded sections at their load
    addresses, from the lowest one, with the gaps zeroed."""
    import io

    elf = ELFFile(io.BytesIO(data))
    loads = [s for s in elf.iter_segments() if s["p_type"] == "PT_LOAD"]
    chunks = []
    for sec in elf.iter_sections():
        if not sec["sh_flags"] & SH_FLAGS.SHF_ALLOC or sec["sh_type"] == "SHT_NOBITS":
            continue
        if not sec["sh_size"]:
            continue
        seg = next(
            s
            for s in loads
            if s["p_offset"] <= sec["sh_offset"] < s["p_offset"] + s["p_filesz"]
        )
        lma = seg["p_paddr"] + sec["sh_offset"] - seg["p_offset"]
        chunks.append((lma, sec.data()))
    base = min(lma for lma, _ in chunks)
    out = bytearray(max(lma + len(d) for lma, d in chunks) - base)
    for lma, d in chunks:
        out[lma - base : lma - base + len(d)] = d
    return bytes(out)
