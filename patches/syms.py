"""Stock firmware symbols, from ``tools/re/<device>.syms``.

Each line is ``<addr> <func|data> <name>  # optional note``. One file feeds the
RE tools (``tools/re/``), the C build (every function becomes the absolute
symbol ``stock_<device>_<name>`` in ``build/stock_symbols.s``) and patch code
(``IntFirmware.sym()``).
"""

from pathlib import Path

# The patcher needs tools/re/ beside patches/ (a package copy must keep both).
REPO = Path(__file__).resolve().parents[1]


def syms_path(device: str) -> Path:
    return REPO / "tools" / "re" / f"{device}.syms"


def parse_syms(path):
    """``(funcs, datas)``, each ``{addr: name}``; function addresses without the Thumb bit."""
    funcs, datas = {}, {}
    for line in path.read_text().splitlines():
        fields = line.split("#", 1)[0].split()
        if not fields:
            continue
        addr, kind, name = int(fields[0], 16), fields[1], fields[2]
        if kind == "func":
            funcs[addr & ~1] = name
        elif kind == "data":
            datas[addr] = name
        else:
            raise ValueError(f"{path}: unknown kind {kind!r} in line {line!r}")
    return funcs, datas


def load(device: str) -> dict:
    """``{name: addr}`` of every function and data symbol of ``device``."""
    funcs, datas = parse_syms(syms_path(device))
    out = {}
    for addr, name in [*funcs.items(), *datas.items()]:
        if name in out:
            raise ValueError(f"{device}.syms: duplicate name {name!r}")
        out[name] = addr
    return out


def c_name(device: str, name: str) -> str:
    """Name our C code calls a stock function by: ``stock_<device>_<name>``.

    The device prefix keeps both models' functions apart in the amalgam build.
    """
    return f"stock_{device}_{name.removeprefix('stock_')}"


def stock_functions(device: str) -> dict:
    """``{C name: Thumb address}`` of every named stock function."""
    funcs, _ = parse_syms(syms_path(device))
    return {c_name(device, name): addr | 1 for addr, name in funcs.items()}
