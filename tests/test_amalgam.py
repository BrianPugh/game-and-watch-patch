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

from scripts.device_from_patch_params import RAM
from test_golden import _copy_tree, _toolchain

FLAGS = "--device=amalgam --game-select-dual-boot --smb1-graphics --triple-boot"


@pytest.fixture(scope="module")
def elf(tmp_path_factory):
    if _toolchain() is None:
        pytest.skip("arm-none-eabi-gcc not found")
    tree = tmp_path_factory.mktemp("amalgam") / "tree"
    _copy_tree(tree)
    proc = subprocess.run(
        ["make", "-j8", f"PYTHON={sys.executable}", f"PATCH_PARAMS={FLAGS}"],
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
        yield syms, sections


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
