"""The C build configuration (``patches/prebuilt.py``) and its prebuilt ELFs."""

import json

import pytest
from test_golden import CONFIGS

from patches import options, prebuilt


@pytest.mark.parametrize("config", CONFIGS)
def test_golden_configs_have_prebuilt(config):
    """Every golden config but --debug's has a prebuilt ELF."""
    argv = CONFIGS[config].format(ips="mod.ips").split()
    name = prebuilt.config_for(argv).name
    if "--debug" in argv:
        assert "debug" in name
    else:
        assert name in prebuilt.combos()


def test_combos_parse_to_their_name():
    for name, params in prebuilt.combos().items():
        assert prebuilt.config_for(params.split()).name == name


def test_abbreviations_rejected():
    assert options.try_parse(["--device=mario", "--triple"]) is None
    assert options.try_parse(["--device=mario", "--sd"]) is None


def test_options_by_meaning_not_text():
    # The icon/label options don't turn the entry on; a path can hold any text.
    config = prebuilt.config_for(
        ["--device=mario", "--game-select-dual-boot-label=X", "--smb1=a/--clock-only"]
    )
    assert config == prebuilt.CConfig("mario", frozenset(), True)


def test_amalgam_sleep():
    # Zelda keeps its backdrops through Mario's --slim.
    assert prebuilt.config_for(["--device=amalgam", "--slim"]).sleep
    assert not prebuilt.config_for(["--device=amalgam", "--no-sleep-images"]).sleep
    assert options.try_parse(["--device=amalgam", "--clock-only"]) is None


def test_makefile():
    config = prebuilt.config_for(["--device=zelda", "--sd-bootloader", "--debug"])
    assert config.makefile() == (
        "GNW_DEVICE := ZELDA\n"
        "CONFIG_DEFS := -DSD_BOOTLOADER -DDEBUG\n"
        "DEBUG := 1\n"
        "SD_BOOTLOADER := 1\n"
        "SLEEP_LDFLAGS := -Wl,--undefined=sleep_image_inflate "
        "-Wl,--undefined=backdrop_inflate\n"
    )


def test_find(tmp_path):
    config = prebuilt.config_for(["--device=mario"])
    with pytest.raises(prebuilt.PrebuiltError, match="manifest.json"):
        prebuilt.find(tmp_path, config)
    (tmp_path / "mario.elf").write_bytes(b"")
    prebuilt.write_manifest(tmp_path, {"mario": "--device=mario"})
    assert prebuilt.find(tmp_path, config) == tmp_path / "mario.elf"

    debug = prebuilt.config_for(["--device=mario", "--debug"])
    with pytest.raises(prebuilt.PrebuiltError, match="--debug"):
        prebuilt.find(tmp_path, debug)
    with pytest.raises(prebuilt.PrebuiltError, match="no "):
        prebuilt.find(tmp_path, prebuilt.config_for(["--device=zelda"]))

    manifest = json.loads((tmp_path / "manifest.json").read_text())
    manifest["patcher"] = "0"
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(prebuilt.PrebuiltError, match="another version"):
        prebuilt.find(tmp_path, config)
