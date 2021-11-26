class MissingSymbolError(Exception):
    """"""


class InvalidStockRomError(Exception):
    """The provided stock ROM did not contain the expected data."""


class InvalidPatchError(Exception):
    """"""


class ParsingError(Exception):
    """"""


class NotEnoughSpaceError(Exception):
    """Not enough storage space in dst to perform the operation."""


class BadImageError(Exception):
    """Provided image is corrupt/wrong dimensions/wrong format"""


class InvalidIPSError(Exception):
    """Corrupt IPS Patch file"""


class InvalidAsmError(Exception):
    """Bad ASM instructions provided to keystone-engine."""
