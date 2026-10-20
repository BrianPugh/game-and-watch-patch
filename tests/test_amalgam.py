"""The amalgam C build (--device=amalgam): one ELF serving both firmwares.

Builds it in a copy of the working tree and checks that each model's RAM is in
that model's window and that both models' stock symbols are there. (Code
both models run can't have RAM; the linker script asserts that.) Needs only
the toolchain (the amalgam doesn't patch, so no stock backups).
"""

import subprocess
import sys

import pytest
from elftools.elf.elffile import ELFFile
from test_golden import _toolchain

from patches.amalgam import SD_BOOTLOADER, ZELDA_DST
from scripts.amalgam_sd import SLACK
from scripts.build_elf_matrix import copy_tree
from scripts.device_from_patch_params import RAM

# --smb1-graphics only needs a file name; nothing reads it without the patch step.
FLAGS = "--device=amalgam --game-select-dual-boot --smb1-graphics mod.ips"


def _build_elf(tmp_path_factory, flags):
    if _toolchain() is None:
        pytest.skip("arm-none-eabi-gcc not found")
    tree = tmp_path_factory.mktemp("amalgam") / "tree"
    copy_tree(tree)
    proc = subprocess.run(
        [
            "make",
            "-j8",
            f"PYTHON={sys.executable}",
            f"PATCH_PARAMS={flags}",
            "build/gw_patch.elf",
        ],
        cwd=tree,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stdout[-4000:] + proc.stderr[-4000:]
    assert not (tree / "build" / "internal_flash_patched.bin").exists()
    with open(tree / "build" / "gw_patch.elf", "rb") as f:
        e = ELFFile(f)
        symtab = e.get_section_by_name(".symtab")
        syms = {s.name: s for s in symtab.iter_symbols() if s.name}
        sections = {s.name: (s["sh_addr"], s["sh_size"]) for s in e.iter_sections()}
    return syms, sections


@pytest.fixture(scope="module")
def elf(tmp_path_factory):
    return _build_elf(tmp_path_factory, FLAGS)


@pytest.fixture(scope="module")
def sd_elf(tmp_path_factory):
    return _build_elf(
        tmp_path_factory, "--device=amalgam --sd-bootloader --smb1-graphics mod.ips"
    )


def _in(model, addr, size=0):
    origin, length = RAM[model]
    return origin <= addr and addr + size <= origin + length


@pytest.mark.parametrize(
    "name, model",
    [
        ("mario_lzma_heap", "mario"),
        ("smb1_graphics_idx", "mario"),
        ("zelda_lzma_heap", "zelda"),
        ("zelda_game_select_view_rows", "zelda"),
        # Bootloader-only (CMSIS system), in Mario's region.
        ("SystemCoreClock", "mario"),
    ],
)
def test_ram_window(elf, name, model):
    syms, _ = elf
    s = syms[name]
    assert _in(model, s["st_value"], s["st_size"])


@pytest.mark.parametrize("model", ["mario", "zelda"])
def test_sections_in_window(elf, model):
    _, sections = elf
    for kind in ("data", "bss"):
        addr, size = sections[f".{model}_{kind}"]
        assert _in(model, addr, size)


def test_stock_symbols(elf):
    syms, _ = elf
    assert syms["stock_mario_read_buttons"]["st_value"] == 0x0801_0D49
    assert syms["stock_zelda_read_buttons"]["st_value"] == 0x0801_6809
    assert "stock_read_buttons" not in syms


def test_sd_bootloader_code_ends_there(sd_elf):
    """With --sd-bootloader, our code is one block past Zelda's code that ends
    by the SD bootloader (scripts/amalgam_sd.py sizes it from the first link)."""
    syms, sections = sd_elf
    start = syms["__our_code__"]["st_value"]
    end = syms["__flash_end__"]["st_value"]
    assert ZELDA_DST < start < end <= SD_BOOTLOADER
    assert SD_BOOTLOADER - end < SLACK + 16
    addr, size = sections[".fill"]
    assert addr + size == SD_BOOTLOADER
