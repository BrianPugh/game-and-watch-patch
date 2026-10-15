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
