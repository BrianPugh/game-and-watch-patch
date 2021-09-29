import sys
from pathlib import Path

targets = sys.argv[1]
if targets in [
    "openocd",
    "gdb",
    "dump",
    "erase_int",
    "erase_ext",
    "flash_stock_int",
    "flash_stock_ext",
]:
    sys.exit()

f = Path(sys.argv[2])
new = sys.argv[3]
old = f.read_text() if f.exists() else ""
if old != new:
    f.write_text(new)
