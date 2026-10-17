import pytest

from patches import syms
from patches.firmware import IntFirmware


def test_c_name():
    assert syms.c_name("mario", "read_buttons") == "stock_mario_read_buttons"
    assert syms.c_name("zelda", "stock_Reset_Handler") == "stock_zelda_Reset_Handler"


@pytest.mark.parametrize(
    "device, name, addr",
    [
        ("mario", "stock_mario_Reset_Handler", 0x0801_7A45),
        ("mario", "stock_mario_read_buttons", 0x0801_0D49),
        ("mario", "stock_mario_ui_tile_ptr", 0x0800_C361),
        ("zelda", "stock_zelda_read_buttons", 0x0801_6809),
    ],
)
def test_stock_functions(device, name, addr):
    assert syms.stock_functions(device)[name] == addr


def test_load_rejects_duplicates(tmp_path, monkeypatch):
    path = tmp_path / "dup.syms"
    path.write_text("0x08000100 func foo\n0x20000000 data foo  # note\n")
    monkeypatch.setattr(syms, "syms_path", lambda device: path)
    with pytest.raises(ValueError, match="duplicate name 'foo'"):
        syms.load("dup")


def test_int_sym():
    image = IntFirmware.__new__(IntFirmware)
    image.extend(bytes(0x2_0000))
    image.syms = syms.load("mario")
    assert image.sym("main_loop_tick") == 0x67E8
    with pytest.raises(ValueError, match="isn't in this image"):
        image.sym("sleep_image_reason")
