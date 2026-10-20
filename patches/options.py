"""The patcher's options, parsed without the stock backups.

``parse(argv)`` gives each model's final options (its ``check_args`` applied),
which both the patch (``patches/cli.py``) and the C build's configuration
(``patches/prebuilt.py``, via ``scripts/device_from_patch_params.py``) use.
"""

import argparse
import contextlib
import io
from pathlib import Path

from . import Device

AMALGAM = "amalgam"


def build_parser():
    """The options every device shares; ``-h`` is added by the caller."""
    parser = argparse.ArgumentParser(
        description="Game and Watch Firmware Patcher. "
        "--help lists the options of the --device given (default mario).",
        add_help=False,
        # Full names only: the amalgam parses argv with each model's parser,
        # where an abbreviation can mean different options.
        allow_abbrev=False,
    )

    #########################
    # Global configurations #
    #########################
    parser.add_argument(
        "--device",
        type=str,
        choices=[
            "mario",
            "zelda",
            "amalgam",
        ],
        default="mario",
        help="Game and Watch device model; amalgam puts both in one 256KB image.",
    )
    parser.add_argument(
        "--patch",
        type=Path,
        default="build/gw_patch.bin",
        help="Compiled custom code to insert at the end of the internal firmware",
    )
    parser.add_argument(
        "--elf",
        type=Path,
        default="build/gw_patch.elf",
        help="ELF file corresponding to the bin provided by --patch",
    )
    parser.add_argument(
        "--prebuilt",
        type=Path,
        help="Directory of prebuilt ELFs (scripts/build_elf_matrix.py); picks "
        "the one built for these options instead of --elf/--patch.",
    )
    parser.add_argument(
        "--backup-dir",
        type=Path,
        default=Path("."),
        help="Directory holding the stock backups "
        "(internal_flash_backup_<device>.bin, flash_backup_<device>.bin).",
    )
    parser.add_argument(
        "--dump-dir",
        type=Path,
        default=Path("build"),
        help="Directory for the debug outputs (decrypted images, manifest, "
        "patched ELF, extracted ROMs and graphics).",
    )
    parser.add_argument(
        "--int-output",
        type=Path,
        default="build/internal_flash_patched.bin",
        help="Patched internal firmware.",
    )
    parser.add_argument(
        "--ext-output",
        type=Path,
        default="build/external_flash_patched.bin",
        help="Patched external firmware.",
    )

    parser.add_argument(
        "--extended",
        action="store_true",
        default=False,
        help="256KB internal flash image instead of 128KB.",
    )
    parser.add_argument(
        "--encrypt",
        action="store_true",
        help="Enable OTFDEC for the main extflash binary.",
    )
    parser.add_argument(
        "--triple-boot",
        action="store_true",
        help="Enable RIGHT+GAME to launch 0x08020000.",
    )
    parser.add_argument(
        "--sd-bootloader",
        action="store_true",
        help="Change target dual boot to 0x08032000.",
    )
    parser.add_argument(
        "--compression-ratio",
        type=float,
        default=1.4,
        help="Data targeted for SRAM3 will only be put into "
        "SRAM3 if it's compression ratio is above this value. "
        "Otherwise, will fallback to internal flash, then external "
        "flash.",
    )

    debugging = parser.add_argument_group("Debugging")
    debugging.add_argument(
        "--show",
        action="store_true",
        help="Show a picture representation of the external patched binary.",
    )
    debugging.add_argument(
        "--debug", action="store_true", help="Install useful debugging fault handlers."
    )

    return parser


def parse(argv, parser=None):
    """``(device, {model: options})`` for ``argv``; ``parser.error`` exits on a
    bad option. The amalgam has both models' options."""
    if parser is None:
        parser = build_parser()
    args, _ = parser.parse_known_args(argv)
    if args.device == AMALGAM:
        from . import amalgam

        return args.device, amalgam.parse(parser, argv)
    cls = Device.registry[args.device]
    cls.add_arguments(parser)
    return args.device, {args.device: cls.check_args(parser.parse_args(argv), parser)}


def try_parse(argv):
    """``parse(argv)``, or None if the options are rejected."""
    with contextlib.redirect_stderr(io.StringIO()):
        try:
            return parse(argv)
        except SystemExit:
            return None
