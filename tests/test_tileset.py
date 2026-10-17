import random

import numpy as np

from patches.tileset import bytes_to_tilemap, render_label_icon, tilemap_to_bytes


def test_tileset_auto():
    data = (80 * np.random.rand(160, 256)).astype(np.uint8).tobytes()
    palette = random.randbytes(80 * 4)

    img = bytes_to_tilemap(data, palette)
    new_data = tilemap_to_bytes(img, palette)

    assert data == new_data


def test_render_label_icon_frame_and_size():
    icon = render_label_icon("DUAL BOOT")
    assert len(icon) == 96 * 48 // 2
    pixels = np.frombuffer(icon, dtype=np.uint8)
    nibbles = np.stack([pixels >> 4, pixels & 0xF], axis=-1).reshape(48, 96)
    assert (nibbles[0, :] == 5).all()  # Top highlight
    assert (nibbles[-1, :] == 7).all()  # Bottom shadow
    assert (nibbles[1:-1, 0] == 5).all()  # Left highlight
    assert (nibbles[1:-1, -1] == 7).all()  # Right shadow
    assert (nibbles[1:-1, 1:-1] == 5).any()  # Some text was drawn


def test_render_label_icon_too_long():
    import pytest

    with pytest.raises(ValueError):
        render_label_icon("W" * 200)


def test_png_to_icon_4bpp_exact_palette(tmp_path):
    from PIL import Image

    from patches.tileset import GAME_SELECT_PALETTE, png_to_icon_4bpp

    nibbles = (
        np.arange(96 * 48, dtype=np.uint8).reshape(48, 96) % 12
    )  # Unambiguous entries 0-11.
    img = Image.new("RGB", (96, 48))
    img.putdata([GAME_SELECT_PALETTE[n] for n in nibbles.flatten()])
    path = tmp_path / "icon.png"
    img.save(path)

    icon = np.frombuffer(png_to_icon_4bpp(path), dtype=np.uint8)
    decoded = np.stack([icon >> 4, icon & 0xF], axis=-1).reshape(48, 96)
    assert (decoded == nibbles).all()


def test_png_to_icon_4bpp_wrong_size(tmp_path):
    import pytest
    from PIL import Image

    from patches.exception import BadImageError
    from patches.tileset import png_to_icon_4bpp

    path = tmp_path / "icon.png"
    Image.new("RGB", (64, 64)).save(path)
    with pytest.raises(BadImageError):
        png_to_icon_4bpp(path)


def test_bundled_retro_go_icon():
    from pathlib import Path

    from patches.tileset import png_to_icon_4bpp

    assert (
        len(png_to_icon_4bpp(Path("patches/assets/retro_go_icon.png"))) == 96 * 48 // 2
    )


def test_render_label_icon_zelda():
    from patches.tileset import ZELDA_ICON_ROLES

    icon = render_label_icon("DUAL BOOT", 96, 32, ZELDA_ICON_ROLES)
    assert len(icon) == 96 * 32 // 2
    pixels = np.frombuffer(icon, dtype=np.uint8)
    nibbles = np.stack([pixels >> 4, pixels & 0xF], axis=-1).reshape(32, 96)
    assert (nibbles[0, :] == ZELDA_ICON_ROLES.highlight).all()
    assert (nibbles[-1, :] == ZELDA_ICON_ROLES.shadow).all()
    assert (nibbles[1:-1, 1:-1] == ZELDA_ICON_ROLES.background).any()


def test_bundled_retro_go_icon_zelda():
    from pathlib import Path

    from patches.tileset import ZELDA_GAME_SELECT_PALETTE, png_to_icon_4bpp

    path = Path("patches/assets/retro_go_icon_zelda.png")
    icon = png_to_icon_4bpp(path, 96, 32, ZELDA_GAME_SELECT_PALETTE)
    assert len(icon) == 96 * 32 // 2
    # Drawn in the menu palette, so every pixel maps exactly.
    from PIL import Image

    with Image.open(path) as img:
        colors = {c for _, c in img.convert("RGB").getcolors()}
    assert colors <= set(ZELDA_GAME_SELECT_PALETTE)
