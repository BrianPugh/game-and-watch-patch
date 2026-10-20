"""The patcher's command line (``patch.py``).

``main(argv)`` runs it from python too (e.g. gnwmanager). Every path it
reads or writes is an option: the stock backups (``--backup-dir``), our
compiled code (``--elf``/``--patch``, or ``--prebuilt``), the outputs and the
debug files (``--dump-dir``).
"""

import sys

import colorama
from colorama import Fore, Style

from . import Device, manifest, options, prebuilt
from .exception import InvalidPatchError
from .layout import Layout


def main(argv=None):
    """Patch the stock firmware; ``argv`` defaults to ``sys.argv[1:]``."""
    if argv is None:
        argv = sys.argv[1:]
    argv = [str(a) for a in argv]
    colorama.init()
    # -h is added once the device's options are, so it lists them too.
    parser = options.build_parser()

    args, _ = parser.parse_known_args(argv)
    parser.add_argument(
        "-h", "--help", action="help", help="Show this help message and exit."
    )
    if {"-h", "--help"} & set(argv):
        if args.device == "amalgam":
            # The 2 devices share some option names.
            parser.conflict_handler = "resolve"
            names = ("zelda", "mario")
        else:
            names = (args.device,)
        for name in names:
            Device.registry[name].add_arguments(parser)
        parser.parse_args(argv)  # Prints the help and exits.
    device_name, models = options.parse(argv)
    args = models["mario" if device_name == "amalgam" else device_name]
    elf, patch = _our_code(parser, args, argv, device_name, models)
    args.dump_dir.mkdir(parents=True, exist_ok=True)
    for path in (args.int_output, args.ext_output):
        path.parent.mkdir(parents=True, exist_ok=True)
    if device_name == "amalgam":
        return main_amalgam(args, models, elf, patch)

    device = Device.registry[device_name](
        args.backup_dir / f"internal_flash_backup_{device_name}.bin",
        elf,
        args.backup_dir / f"flash_backup_{device_name}.bin",
    )
    device.args = args
    device.crypt()  # Decrypt the external firmware

    dump_dir = args.dump_dir
    # Save the decrypted external firmware for debugging/development purposes.
    (dump_dir / "decrypt.bin").write_bytes(device.external)

    # Dump ITCM and DTCM RAM data
    if (
        device.internal.RWDATA_OFFSET is not None
        and device.internal.RWDATA_ITCM_IDX is not None
    ):
        (dump_dir / "itcm_rwdata.bin").write_bytes(
            device.internal.rwdata.datas[device.internal.RWDATA_ITCM_IDX]
        )
    if (
        device.internal.RWDATA_OFFSET is not None
        and device.internal.RWDATA_DTCM_IDX is not None
    ):
        (dump_dir / "dtcm_rwdata.bin").write_bytes(
            device.internal.rwdata.datas[device.internal.RWDATA_DTCM_IDX]
        )

    # Copy over novel code
    if len(device.internal) != len(patch):
        raise InvalidPatchError(
            f"Expected patch length {len(device.internal)}, got {len(patch)}"
        )

    # novel_code_start = device.internal.address("__do_global_dtors_aux") & 0x00FF_FFF8
    novel_code_start = device.internal.STOCK_ROM_END
    device.internal[novel_code_start:] = patch[novel_code_start:]
    del patch

    if args.sd_bootloader:
        device.internal.extend(b"\x00" * 0x12000)
    elif args.extended:
        device.internal.extend(b"\x00" * 0x20000)

    print(Fore.BLUE)
    print("#########################")
    print("# BEGINING BINARY PATCH #")
    print("#########################" + Style.RESET_ALL)

    layout = Layout()
    layout.add(device)
    layout.patch()  # Apply patches
    internal_remaining_free = device.int_remaining_free
    compressed_memory_remaining_free = device.sram3_remaining_free

    if args.show:
        # Debug visualization
        device.show()

    # Re-encrypt the external firmware
    (dump_dir / "decrypt_flash_patched.bin").write_bytes(device.external)
    images = [
        manifest.DeviceImages(device, device.internal, device.bank2, device.external)
    ]
    debug_outputs = write_debug_outputs(
        args, images, device.internal, device.bank2, device.external
    )
    if args.encrypt:
        device.external.crypt(device.internal.key, device.internal.nonce)

    # Save patched firmware
    args.int_output.write_bytes(device.internal)
    args.ext_output.write_bytes(device.external)
    bank2_output = args.int_output.with_name(args.int_output.stem + "_bank2.bin")
    if device.bank2:
        bank2_output.write_bytes(device.bank2)
        print(f"Wrote relocated stock firmware to {bank2_output}")
    elif bank2_output.exists():
        bank2_output.unlink()
    map_output = args.dump_dir / "layout.txt"
    map_output.write_text("\n".join(layout.map_lines()) + "\n")

    print(Fore.GREEN)
    print("Binary Patching Complete!")
    print(
        f"    Internal Firmware Used:  {len(device.internal) - internal_remaining_free} bytes"
    )
    print(f"        Free: {internal_remaining_free} bytes")
    if device.int_space.holes:
        holes = ", ".join(
            f"{end - start}" for start, end in device.int_space.holes if end > start
        )
        print(
            f"          of which in freed stock-code holes: {device.int_space.hole_space} bytes ({holes})"
        )
    print(
        f"    Compressed Memory Used: {len(device.compressed_memory) - compressed_memory_remaining_free} bytes"
    )
    print(f"        Free: {compressed_memory_remaining_free} bytes")
    print(f"    External Firmware Used: {len(device.external)} bytes")
    # Relocated data spills between regions, so per-region "Free" depends on
    # placement order. The total is the number to minimize.
    print(
        f"    Total Footprint (internal + external): "
        f"{len(device.internal) - internal_remaining_free + len(device.external)} bytes"
    )
    print(f"    Allocation map: {map_output}")
    print(debug_outputs)
    print(Style.RESET_ALL)


def write_debug_outputs(args, images, internal, bank2, external):
    """The patch manifest and the patched ELF; returns lines naming them."""
    out = args.dump_dir
    manifest_output = out / "manifest.txt"
    manifest.write_manifest(manifest_output, images)
    lines = f"    Patch manifest: {manifest_output}"
    elf_output = out / "patched.elf"
    elf = images[-1].d.internal.elf
    if manifest.write_elf(
        elf_output, images, internal, bank2, external, elf, out / "patched_elf"
    ):
        lines += f"\n    Patched ELF: {elf_output}"
    return lines


def _our_code(parser, args, argv, device, models):
    """``(ELF path, image)`` of our compiled code: ``--elf``/``--patch``, or the
    ``--prebuilt`` ELF built for these options."""
    if not args.prebuilt:
        return args.elf, args.patch.read_bytes()
    if {a.split("=")[0] for a in argv} & {"--elf", "--patch"}:
        parser.error("--prebuilt replaces --elf and --patch")
    try:
        elf = prebuilt.find(args.prebuilt, prebuilt.c_config(device, models))
    except prebuilt.PrebuiltError as e:
        parser.error(str(e))
    return elf, prebuilt.elf_to_bin(elf.read_bytes())


def main_amalgam(args, models, elf, patch):
    """Mario and Zelda in one image; see patches/amalgam.py."""
    from . import amalgam

    zelda, mario = (
        Device.registry[name](
            args.backup_dir / f"internal_flash_backup_{name}.bin",
            elf,
            args.backup_dir / f"flash_backup_{name}.bin",
        )
        for name in ("zelda", "mario")
    )
    amalgam.configure(zelda, mario, models)
    for device in (zelda, mario):
        device.crypt()  # Decrypt the external firmware
    amalgam.load((zelda, mario), patch)

    print(Fore.BLUE)
    print("#########################")
    print("# BEGINING BINARY PATCH #")
    print("#########################" + Style.RESET_ALL)

    layout = Layout()
    bank1 = amalgam.bank1_region(zelda, mario)
    layout.regions.append(bank1)
    for device in (zelda, mario):
        layout.add(device, bank1)
    amalgam.patch(layout, zelda, mario)

    internal = amalgam.merge_internal(bank1, [zelda, mario])
    external = amalgam.merge_external(zelda, mario)
    amalgam.metadata(internal, external)
    if args.sd_bootloader:
        # Flashing it leaves the SD bootloader alone.
        del internal[amalgam.SD_BOOTLOADER - amalgam.BASE :]
    images = [
        manifest.DeviceImages(device, internal, None, external)
        for device in (zelda, mario)
    ]
    debug_outputs = write_debug_outputs(args, images, internal, None, external)

    args.int_output.write_bytes(internal)
    args.ext_output.write_bytes(external)
    bank2_output = args.int_output.with_name(args.int_output.stem + "_bank2.bin")
    if bank2_output.exists():
        bank2_output.unlink()
    map_output = args.dump_dir / "layout.txt"
    map_output.write_text("\n".join(layout.map_lines()) + "\n")

    free = bank1.limit - bank1.pos + bank1.hole_space
    print(Fore.GREEN)
    print("Binary Patching Complete!")
    print(f"    Internal Firmware Used:  {len(internal) - free} bytes")
    print(f"        Free: {free} bytes")
    for device in (zelda, mario):
        used = device.sram3_space.pos
        print(f"    {device.name} Compressed Memory Used: {used} bytes")
    print(f"    External Firmware Used: {len(external)} bytes")
    print(f"        Mario's data starts at 0x{mario.ext_base:06X}")
    print(f"    Allocation map: {map_output}")
    print(debug_outputs)
    print(Style.RESET_ALL)


if __name__ == "__main__":
    main()
