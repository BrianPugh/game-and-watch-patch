"""Our compiled code's build configuration, and prebuilt copies of it for
patching without a compiler.

The C build depends on a few patch options. ``c_config()`` derives that
configuration from the parsed options (``patches/options.py``); the Makefile
gets it from ``scripts/device_from_patch_params.py`` (``build/config.mk``), so
there's one set of rules. ``combos()`` lists every configuration the options
can ask for, ``scripts/build_elf_matrix.py`` builds one ELF per configuration
plus a ``manifest.json``, and ``patch.py --prebuilt <dir>`` (``find()``) picks
the ELF its options need.
"""

import hashlib
import io
import itertools
import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from elftools.elf.constants import SH_FLAGS
from elftools.elf.elffile import ELFFile

from . import options

REPO = Path(__file__).resolve().parents[1]

# (C define, ELF name part), in name order. DEBUG has no prebuilt ELF.
DEFINES = [
    ("SD_BOOTLOADER", "sd"),
    ("TRIPLE_BOOT", "triple"),
    ("CLOCK_ONLY", "clock"),
    ("ENABLE_SMB1_GRAPHIC_MODS", "smb1"),
    ("GAME_SELECT_DUAL_BOOT", "gsdb"),
    ("DEBUG", "debug"),
]
# Linked only while some model keeps its sleep images (Zelda: backdrops).
SLEEP_SYMBOLS = ("sleep_image_inflate", "backdrop_inflate")
# The options combos() combines (--smb1-graphics needs a file name; the
# configuration only looks at whether there is one).
MATRIX_OPTIONS = [
    ["--sd-bootloader"],
    ["--triple-boot"],
    ["--clock-only"],
    ["--smb1-graphics", "mod.ips"],
    ["--game-select-dual-boot"],
    ["--no-sleep-images"],
]
# What a manifest.json records the hashes of: the patcher (what gnwmanager
# ships) and everything else the C build reads.
PATCHER_FILES = ["patches/**/*.py", "patches/assets/*", "tools/re/*.syms"]
SOURCE_FILES = ["Core/**/*", "Drivers/**/*", "Makefile*", "*.ld", "scripts/*.py"]


class PrebuiltError(Exception):
    pass


@dataclass(frozen=True)
class CConfig:
    device: str
    defines: frozenset
    sleep: bool  # link SLEEP_SYMBOLS

    @property
    def name(self):
        parts = [self.device, *(n for d, n in DEFINES if d in self.defines)]
        if not self.sleep:
            parts.append("nosleep")
        return "-".join(parts)

    def makefile(self):
        """``build/config.mk``, which the Makefile includes."""
        defs = " ".join(f"-D{d}" for d, _ in DEFINES if d in self.defines)
        lines = [
            f"GNW_DEVICE := {self.device.upper()}",
            f"CONFIG_DEFS := {defs}",
            f"DEBUG := {int('DEBUG' in self.defines)}",
            f"SD_BOOTLOADER := {int('SD_BOOTLOADER' in self.defines)}",
        ]
        undefined = " ".join(f"-Wl,--undefined={s}" for s in SLEEP_SYMBOLS)
        lines.append(f"SLEEP_LDFLAGS := {undefined if self.sleep else ''}")
        return "\n".join(lines) + "\n"


def c_config(device, models):
    """The C build configuration ``options.parse()``'s result needs."""

    def anyone(attr):
        return any(getattr(a, attr, None) for a in models.values())

    flags = {
        "SD_BOOTLOADER": anyone("sd_bootloader"),
        "TRIPLE_BOOT": anyone("triple_boot"),
        "CLOCK_ONLY": anyone("clock_only"),
        "ENABLE_SMB1_GRAPHIC_MODS": anyone("smb1_graphics"),
        "GAME_SELECT_DUAL_BOOT": anyone("game_select_dual_boot"),
        "DEBUG": anyone("debug"),
    }
    defines = frozenset(d for d, on in flags.items() if on)
    sleep = not all(a.no_sleep_images for a in models.values())
    return CConfig(device, defines, sleep)


def config_for(argv):
    """``c_config()`` of a command line; exits on a bad option."""
    return c_config(*options.parse(argv))


def combos():
    """``{name: PATCH_PARAMS}`` of every configuration but ``--debug``'s."""
    out = {}
    for device in ("mario", "zelda", options.AMALGAM):
        for picked in itertools.product((False, True), repeat=len(MATRIX_OPTIONS)):
            argv = [f"--device={device}"]
            argv += [a for opt, p in zip(MATRIX_OPTIONS, picked) if p for a in opt]
            parsed = options.try_parse(argv)
            if parsed is None:
                continue  # rejected, e.g. Zelda has no --clock-only
            name = c_config(*parsed).name
            if name not in out or len(argv) < len(out[name].split()):
                out[name] = " ".join(argv)
    return out


def _hash(root, patterns):
    h = hashlib.sha256()
    paths = sorted({p for pat in patterns for p in root.glob(pat) if p.is_file()})
    for path in paths:
        if "__pycache__" in path.parts:
            continue
        h.update(path.relative_to(root).as_posix().encode() + b"\0")
        h.update(hashlib.sha256(path.read_bytes()).digest())
    return h.hexdigest()


def write_manifest(out, elfs, root=REPO):
    """``<out>/manifest.json``: what the ELFs in ``out`` were built from."""

    def git(*cmd):
        proc = subprocess.run(["git", *cmd], cwd=root, capture_output=True, text=True)
        return proc.stdout.strip() if proc.returncode == 0 else None

    gcc = shutil.which("arm-none-eabi-gcc")
    toolchain = None
    if gcc:
        version = subprocess.run([gcc, "--version"], capture_output=True, text=True)
        toolchain = version.stdout.splitlines()[0]
    manifest = {
        "patcher": _hash(root, PATCHER_FILES),
        "sources": _hash(root, SOURCE_FILES),
        "commit": git("rev-parse", "HEAD"),
        "dirty": bool(git("status", "--porcelain", "--untracked-files=no")),
        "toolchain": toolchain,
        "elfs": elfs,
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")


def find(directory, config, root=REPO):
    """The ELF in ``directory`` (``write_manifest()``'s) built for ``config``."""
    if "DEBUG" in config.defines:
        raise PrebuiltError("--debug needs a build from source")
    try:
        manifest = json.loads((directory / "manifest.json").read_text())
    except FileNotFoundError:
        raise PrebuiltError(f"--prebuilt: no {directory / 'manifest.json'}")
    if manifest["patcher"] != _hash(root, PATCHER_FILES):
        raise PrebuiltError(
            f"--prebuilt: {directory} was built for another version of the patcher"
        )
    if (root / "Core").is_dir() and manifest["sources"] != _hash(root, SOURCE_FILES):
        raise PrebuiltError(
            f"--prebuilt: {directory} was built from other C sources; rebuild it "
            "(python -m scripts.build_elf_matrix)"
        )
    path = directory / f"{config.name}.elf"
    if config.name not in manifest["elfs"] or not path.exists():
        raise PrebuiltError(f"--prebuilt: no {path}")
    return path


def elf_to_bin(data):
    """``objcopy -O binary`` of an ELF: its loaded sections at their load
    addresses, from the lowest one, with the gaps zeroed."""
    elf = ELFFile(io.BytesIO(data))
    loads = [s for s in elf.iter_segments() if s["p_type"] == "PT_LOAD"]
    chunks = []
    for sec in elf.iter_sections():
        if not sec["sh_flags"] & SH_FLAGS.SHF_ALLOC or sec["sh_type"] == "SHT_NOBITS":
            continue
        if not sec["sh_size"]:
            continue
        seg = next(
            (
                s
                for s in loads
                if s["p_offset"] <= sec["sh_offset"] < s["p_offset"] + s["p_filesz"]
            ),
            None,
        )
        if seg is None:
            raise PrebuiltError(f"section {sec.name} isn't in a loaded segment")
        lma = seg["p_paddr"] + sec["sh_offset"] - seg["p_offset"]
        chunks.append((lma, sec.data()))
    base = min(lma for lma, _ in chunks)
    out = bytearray(max(lma + len(d) for lma, d in chunks) - base)
    for lma, d in chunks:
        out[lma - base : lma - base + len(d)] = d
    return bytes(out)
