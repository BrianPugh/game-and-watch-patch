"""
For usage, run:
        python3 patch.py --help
"""

import sys

if sys.version_info[0] < 3 or sys.version_info[1] < 6:
    raise Exception("Must be using at least Python 3.6")


import argparse
from pathlib import Path

import colorama
from colorama import Fore, Style

from patches import Device
from patches.exception import InvalidPatchError

colorama.init()


def main():
    parser = argparse.ArgumentParser(description="Game and Watch Firmware Patcher.")

    #########################
    # Global configurations #
    #########################
    parser.add_argument(
        "--device",
        type=str,
        choices=[
            "mario",
            "zelda",
        ],
        default="mario",
        help="Game and Watch device model",
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

    args, _ = parser.parse_known_args()
    args.int_firmware = Path(f"internal_flash_backup_{args.device}.bin")
    args.ext_firmware = Path(f"flash_backup_{args.device}.bin")

    device = Device.registry[args.device](
        args.int_firmware, args.elf, args.ext_firmware
    )
    args = device.argparse(parser)
    device.crypt()  # Decrypt the external firmware

    # Save the decrypted external firmware for debugging/development purposes.
    Path("build/decrypt.bin").write_bytes(device.external)

    # Dump ITCM and DTCM RAM data
    if (
        device.internal.RWDATA_OFFSET is not None
        and device.internal.RWDATA_ITCM_IDX is not None
    ):
        Path("build/itcm_rwdata.bin").write_bytes(
            device.internal.rwdata.datas[device.internal.RWDATA_ITCM_IDX]
        )
    if (
        device.internal.RWDATA_OFFSET is not None
        and device.internal.RWDATA_DTCM_IDX is not None
    ):
        Path("build/dtcm_rwdata.bin").write_bytes(
            device.internal.rwdata.datas[device.internal.RWDATA_DTCM_IDX]
        )

    # Copy over novel code
    patch = args.patch.read_bytes()
    if len(device.internal) != len(patch):
        raise InvalidPatchError(
            f"Expected patch length {len(device.internal)}, got {len(patch)}"
        )

    # novel_code_start = device.internal.address("__do_global_dtors_aux") & 0x00FF_FFF8
    novel_code_start = device.internal.STOCK_ROM_END
    device.internal[novel_code_start:] = patch[novel_code_start:]
    del patch

    if args.extended:
        device.internal.extend(b"\x00" * 0x20000)

    print(Fore.BLUE)
    print("#########################")
    print("# BEGINING BINARY PATCH #")
    print("#########################" + Style.RESET_ALL)

    (
        internal_remaining_free,
        compressed_memory_remaining_free,
    ) = device()  # Apply patches

    if args.show:
        # Debug visualization
        device.show()

    # Re-encrypt the external firmware
    Path("build/decrypt_flash_patched.bin").write_bytes(device.external)
    if args.encrypt:
        device.external.crypt(device.internal.key, device.internal.nonce)

    # Save patched firmware
    args.int_output.write_bytes(device.internal)
    args.ext_output.write_bytes(device.external)

    print(Fore.GREEN)
    print("Binary Patching Complete!")
    print(
        f"    Internal Firmware Used:  {len(device.internal) - internal_remaining_free} bytes"
    )
    print(f"        Free: {internal_remaining_free} bytes")
    print(
        f"    Compressed Memory Used: {len(device.compressed_memory) - compressed_memory_remaining_free} bytes"
    )
    print(f"        Free: {compressed_memory_remaining_free} bytes")
    print(f"    External Firmware Used: {len(device.external)} bytes")
    print(Style.RESET_ALL)


if __name__ == "__main__":
    main()
