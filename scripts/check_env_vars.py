import sys
from pathlib import Path

new = " ".join(sys.argv[2:])
f = Path(sys.argv[1])
old = f.read_text() if f.exists() else ""
if old != new:
    f.write_text(new)
