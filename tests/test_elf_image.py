import shutil
import subprocess

import pytest

from patches import elf_image

REAL_LD = shutil.which("arm-none-eabi-ld")
pytestmark = pytest.mark.skipif(
    REAL_LD is None or shutil.which("arm-none-eabi-objcopy") is None,
    reason="needs arm-none-eabi binutils",
)


def _old_toolchain(tmp_path):
    """A toolchain prefix whose ``ld`` predates ``--no-warn-rwx-segments``
    (binutils < 2.39, e.g. Debian bullseye)."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    ld = bin_dir / "arm-none-eabi-ld"
    ld.write_text(
        "#!/bin/sh\n"
        f'if [ "$1" = --help ]; then "{REAL_LD}" --help | grep -v rwx-segments; exit; fi\n'
        'for a in "$@"; do\n'
        '  [ "$a" = --no-warn-rwx-segments ] && echo "unrecognized option $a" >&2 && exit 1\n'
        "done\n"
        f'exec "{REAL_LD}" "$@"\n'
    )
    ld.chmod(0o755)
    (bin_dir / "arm-none-eabi-objcopy").symlink_to(
        shutil.which("arm-none-eabi-objcopy")
    )
    return f"{bin_dir}/arm-none-eabi-"


def _link(tmp_path, prefix):
    out = tmp_path / "out.elf"
    code = bytes([0x70, 0x47]) * 8  # bx lr
    elf_image.link(
        out,
        [(".intflash", 0x0800_0000, code, True)],
        [(0x0800_0000, "f")],
        [],
        [(0x0800_0000, "$t")],
        0x0800_0000,
        tmp_path / "work",
        prefix,
    )
    return out


def test_link(tmp_path):
    assert _link(tmp_path, "arm-none-eabi-").read_bytes()[:4] == b"\x7fELF"


def test_link_with_old_ld(tmp_path):
    prefix = _old_toolchain(tmp_path)
    with pytest.raises(subprocess.CalledProcessError):
        subprocess.run(
            [f"{prefix}ld", "--no-warn-rwx-segments"], check=True, capture_output=True
        )
    assert _link(tmp_path, prefix).read_bytes()[:4] == b"\x7fELF"
