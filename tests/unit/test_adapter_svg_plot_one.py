from __future__ import annotations

from pathlib import Path

from kicad_pcb.adapters import FakeFs, FakeRunner, KicadCliAdapter
from kicad_pcb.compat import KiCadVersion


def test_export_svg_sch_plot_one_args() -> None:
    runner = FakeRunner()
    cli = KicadCliAdapter(runner=runner, fs=FakeFs(), version=KiCadVersion(9, 0, 0))
    schematic = Path("/proj/board.kicad_sch")
    output_dir = Path("/proj/preview")

    cli.export_svg_sch(schematic, output_dir, plot_one=True)

    assert runner.calls[-1] == [
        "kicad-cli",
        "sch",
        "export",
        "svg",
        "--plot-one",
        "--output",
        str(output_dir),
        str(schematic),
    ]
