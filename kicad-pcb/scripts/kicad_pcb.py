#!/usr/bin/env python3
"""Thin entry-point wrapper — logic lives in the kicad_pcb package (kicad-pcb/src/).

When run directly as a script (e.g. ``python kicad_pcb.py new myproject``),
this file adds the sibling ``src/`` directory to *sys.path* so the
``kicad_pcb`` package can be imported without a prior ``pip install``.
"""
import sys
from pathlib import Path

_src = Path(__file__).parent.parent / "src"
if str(_src) not in sys.path:
    sys.path.insert(0, str(_src))

from kicad_pcb.cli import main  # noqa: E402

if __name__ == "__main__":
    main()
