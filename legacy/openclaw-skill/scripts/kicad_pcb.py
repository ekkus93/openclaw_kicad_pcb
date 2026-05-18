#!/home/ubo/work/openclaw_kicad_pcb/.venv/bin/python3
"""Thin entry-point wrapper — logic lives in the repository ``src/`` tree.

When run directly as a script (e.g. ``python kicad_pcb.py new myproject``),
this file adds the repository ``src/`` directory to *sys.path* so the
``kicad_pcb`` package can be imported without a prior ``pip install``.
"""

import contextlib
import sys
from pathlib import Path

_src = Path(__file__).resolve().parents[3] / "src"
# Always move _src to position 0 so the package in src/ is found before this
# script file (kicad_pcb.py in scripts/) which shares the same base name.
# An editable install may already have _src later in sys.path via a .pth file,
# so a simple "if not in sys.path" guard is insufficient.
_src_str = str(_src)
with contextlib.suppress(ValueError):
    sys.path.remove(_src_str)
sys.path.insert(0, _src_str)

from kicad_pcb.cli import main  # noqa: E402

if __name__ == "__main__":
    main()
