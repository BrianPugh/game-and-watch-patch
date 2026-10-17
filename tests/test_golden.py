"""Whole-build regression test: patch fixed flag sets, compare output hashes.

Each config builds a copy of the working tree (C code and patch) and checks the
SHA1 of every output image against ``tests/golden.json``. The C build makes the
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
    "--no-second-beep --no-hour-tune --encrypt --debug",
    "zelda_relocate": "--device=zelda --relocate-stock=0x08120000",
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


def _build(tmp_path, params):
    tree = tmp_path / "tree"
    _copy_tree(tree)
    device = params.split()[0].split("=")[1]
    for name in (f"internal_flash_backup_{device}.bin", f"flash_backup_{device}.bin"):
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
    return hashes


@pytest.mark.parametrize("config", CONFIGS)
def test_golden(config, tmp_path):
    device = CONFIGS[config].split()[0].split("=")[1]
    for name in (f"internal_flash_backup_{device}.bin", f"flash_backup_{device}.bin"):
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
