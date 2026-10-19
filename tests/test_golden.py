"""Whole-build regression test: patch fixed flag sets, compare output hashes.

Each config builds a copy of the working tree (C code and patch) and checks the
SHA1 of every output image against ``tests/golden.json``, and that the patch
manifest and patched ELF (``patches/manifest.py``) are complete. The C build makes the
hashes depend on the toolchain, so they're recorded with the gcc version and
the test skips under a different one. Skips without the stock backups.

When a change is meant to alter the output, re-record and commit the new hashes
with it:

    UPDATE_GOLDEN=1 .venv/bin/python -m pytest tests/test_golden.py
"""

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from elftools.elf.elffile import ELFFile

from patches import syms

REPO = Path(__file__).resolve().parents[1]
GOLDEN = Path(__file__).with_name("golden.json")
UPDATE = os.environ.get("UPDATE_GOLDEN") == "1"

# An empty IPS patch; exercises the --smb1-graphics table and its C code.
EMPTY_IPS = b"PATCHEOF"

CONFIGS = {
    "mario": "--device=mario",
    "mario_internal_only": "--device=mario --internal-only",
    "mario_relocate": "--device=mario --slim --no-factory-test --no-mario-song "
    "--mario-song-time=5 --relocate-stock=0x08120000",
    "mario_options": "--device=mario --game-select-dual-boot --sleep-time=5 "
    "--sleep-on-usb --hard-reset-time=3 --mario-song-time=5 --no-save --encrypt "
    "--triple-boot --smb1-graphics {ips}",
    "mario_no_song": "--device=mario --no-mario-song",
    "mario_clock_only": "--device=mario --clock-only --disable-sleep --debug",
    "zelda": "--device=zelda",
    "zelda_options": "--device=zelda --no-la --no-sleep-images --no-factory-test "
    "--no-hour-tune --encrypt --debug --game-select-dual-boot",
    "zelda_relocate": "--device=zelda --relocate-stock=0x08120000",
    "zelda_relocate_bank1": "--device=zelda --extended --relocate-stock=0x08020000",
    # Both firmwares in one image; --no-factory-test frees stock code in both,
    # so each device's data lands in the other's holes.
    "amalgam": "--device=amalgam --game-select-dual-boot --no-factory-test",
    # BALL and Vermin run from external flash; Zelda's stock code moves too.
    "mario_xip": "--device=mario --xip-minigame",
    "zelda_xip_relocate": "--device=zelda --xip-minigame --relocate-stock=0x08120000",
    "amalgam_xip": "--device=amalgam --no-factory-test --xip-minigame",
}

OUTPUTS = [
    "internal_flash_patched.bin",
    "internal_flash_patched_bank2.bin",
    "external_flash_patched.bin",
]


def _toolchain():
    gcc = os.environ.get("GCC_PATH")
    gcc = str(Path(gcc) / "arm-none-eabi-gcc") if gcc else "arm-none-eabi-gcc"
    if not shutil.which(gcc):
        return None
    out = subprocess.run([gcc, "--version"], capture_output=True, text=True)
    return out.stdout.splitlines()[0]


def _copy_tree(dst):
    files = subprocess.run(
        ["git", "ls-files", "-co", "--exclude-standard", "-z"],
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


def _models(params):
    device = params.split()[0].split("=")[1]
    return ["mario", "zelda"] if device == "amalgam" else [device]


def _backups(params):
    return [
        name
        for m in _models(params)
        for name in (f"internal_flash_backup_{m}.bin", f"flash_backup_{m}.bin")
    ]


def _build(tmp_path, params):
    tree = tmp_path / "tree"
    _copy_tree(tree)
    for name in _backups(params):
        (tree / name).symlink_to(REPO / name)
    ips = tmp_path / "empty.ips"
    ips.write_bytes(EMPTY_IPS)
    params = params.format(ips=ips)
    proc = subprocess.run(
        ["make", "-j8", f"PYTHON={sys.executable}", f"PATCH_PARAMS={params}"],
        cwd=tree,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stdout[-4000:] + proc.stderr[-4000:]
    hashes = {}
    for name in OUTPUTS:
        path = tree / "build" / name
        if path.exists():
            hashes[name] = hashlib.sha1(path.read_bytes()).hexdigest()
    _check_debug_outputs(tree, params)
    return hashes


def _check_debug_outputs(tree, params):
    """The patch manifest names every change, and the patched ELF has each
    model's stock ``main`` where its code is now."""
    header = (tree / "build" / "manifest.txt").read_text().splitlines()[2]
    assert header.endswith(" 0 unattributed"), header

    with open(tree / "build" / "patched.elf", "rb") as f:
        elf = ELFFile(f)
        symtab = elf.get_section_by_name(".symtab")
        assert symtab.get_symbol_by_name("bootloader")
        for model in _models(params):
            stock = (REPO / f"internal_flash_backup_{model}.bin").read_bytes()
            main = syms.load(model)["main"]
            [sym] = symtab.get_symbol_by_name(syms.c_name(model, "main"))
            addr = sym["st_value"] & ~1
            sec = elf.get_section(sym["st_shndx"])
            data = sec.data()[addr - sec["sh_addr"] :][:16]
            assert data == stock[main - 0x0800_0000 :][:16], model


@pytest.mark.parametrize("config", CONFIGS)
def test_golden(config, tmp_path):
    for name in _backups(CONFIGS[config]):
        if not (REPO / name).exists():
            pytest.skip(f"missing {name}")
    toolchain = _toolchain()
    if toolchain is None:
        pytest.skip("arm-none-eabi-gcc not found")

    golden = json.loads(GOLDEN.read_text()) if GOLDEN.exists() else {}
    if not UPDATE and golden.get("toolchain") != toolchain:
        pytest.skip(f"hashes recorded with {golden.get('toolchain')!r}")

    hashes = _build(tmp_path, CONFIGS[config])

    if UPDATE:
        if golden.get("toolchain") != toolchain:
            golden = {"toolchain": toolchain, "configs": {}}
        golden["configs"][config] = hashes
        GOLDEN.write_text(json.dumps(golden, indent=2, sort_keys=True) + "\n")
    else:
        assert hashes == golden["configs"][config]
