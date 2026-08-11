from pathlib import Path

from kicad_pcb.compat import KiCadVersion
from kicad_pcb.refinement.rendering import render_schematic_for_refinement


class _Result:
    ok = True
    returncode = 0


class _Adapter:
    detected_version = KiCadVersion(9, 0, 0)
    def export_svg_sch(self, _sch: Path, output: Path):
        output.write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" '
            'viewBox="0 0 297 210"></svg>',
            encoding="utf-8",
        )
        return _Result()


class _Rasterizer:
    def rasterize(self, _svg: Path, png: Path) -> None:
        png.write_bytes(
            b"\x89PNG\r\n\x1a\n"
            + b"\x00\x00\x00\x0dIHDR"
            + (297).to_bytes(4, "big")
            + (210).to_bytes(4, "big")
            + b"\x08\x02\x00\x00\x00"
        )


def test_render_binds_hashes_and_transform(tmp_path: Path) -> None:
    fixture = (
        Path(__file__).parents[1]
        / "fixtures"
        / "readability"
        / "ne5532_headphone_amp_left_current"
        / "baseline_generated.kicad_sch"
    )
    artifact = render_schematic_for_refinement(
        fixture,
        tmp_path / "out",
        adapter=_Adapter(),  # type: ignore[arg-type]
        rasterizer=_Rasterizer(),
    )
    assert artifact.width_px == 297
    assert artifact.pixels_per_mm_x == 1.0
    assert len(artifact.schematic_hash) == 64
    assert len(artifact.png_hash) == 64
