from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from kicad_pcb.compat import KiCadVersion
from kicad_pcb.errors import UserError
from kicad_pcb.refinement.rendering import (
    MAX_REVIEW_REGION_DIMENSION_PX,
    REVIEW_PIXELS_PER_MM,
    render_schematic_for_refinement,
)


class _Result:
    ok = True
    returncode = 0


class _Adapter:
    detected_version = KiCadVersion(9, 0, 0)

    def __init__(self, width_mm: float = 297.0, height_mm: float = 210.0) -> None:
        self.width_mm = width_mm
        self.height_mm = height_mm

    def export_svg_sch(self, _sch: Path, output: Path, *, plot_one: bool = False):
        assert output.is_dir()
        assert plot_one
        (output / "rendered-sheet.svg").write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" '
            f'viewBox="0 0 {self.width_mm:g} {self.height_mm:g}"></svg>',
            encoding="utf-8",
        )
        return _Result()


class _Rasterizer:
    def rasterize(self, svg: Path, png: Path) -> None:
        root = ET.parse(svg).getroot()
        width = _svg_dimension_px(root, "width", fallback_index=2)
        height = _svg_dimension_px(root, "height", fallback_index=3)
        png.write_bytes(
            b"\x89PNG\r\n\x1a\n"
            + b"\x00\x00\x00\x0dIHDR"
            + width.to_bytes(4, "big")
            + height.to_bytes(4, "big")
            + b"\x08\x02\x00\x00\x00"
        )


def _svg_dimension_px(root: ET.Element, attribute: str, *, fallback_index: int) -> int:
    raw = root.attrib.get(attribute)
    if raw is not None and raw.endswith("px"):
        return int(round(float(raw[:-2])))
    values = tuple(float(value) for value in root.attrib["viewBox"].split())
    return int(round(values[fallback_index]))


def _fixture() -> Path:
    return (
        Path(__file__).parents[1]
        / "fixtures"
        / "readability"
        / "ne5532_headphone_amp_left_current"
        / "baseline_generated.kicad_sch"
    )


def _fixture_with_paper(tmp_path: Path, paper: str) -> Path:
    target = tmp_path / f"{paper}.kicad_sch"
    target.write_text(
        _fixture().read_text(encoding="utf-8").replace('(paper "A4")', f'(paper "{paper}")', 1),
        encoding="utf-8",
    )
    return target


def test_render_binds_hashes_transform_and_readable_review_region(tmp_path: Path) -> None:
    artifact = render_schematic_for_refinement(
        _fixture(),
        tmp_path / "out",
        adapter=_Adapter(),  # type: ignore[arg-type]
        rasterizer=_Rasterizer(),
    )

    assert artifact.schema_version == "1.1"
    assert artifact.width_px == 297
    assert artifact.pixels_per_mm_x == 1.0
    assert len(artifact.schematic_hash) == 64
    assert len(artifact.png_hash) == 64
    assert len(artifact.review_regions) == 1
    region = artifact.review_regions[0]
    assert region.region_id == "r00-c00"
    assert region.image_index == 0
    assert region.view_box_mm == (0.0, 0.0, 297.0, 210.0)
    assert (region.width_px, region.height_px) == (1188, 840)
    assert region.pixels_per_mm_x == pytest.approx(REVIEW_PIXELS_PER_MM)
    assert region.pixels_per_mm_y == pytest.approx(REVIEW_PIXELS_PER_MM)
    assert artifact.review_image_paths == (region.png_path,)


def test_render_accepts_kicad_svg_page_rounding(tmp_path: Path) -> None:
    artifact = render_schematic_for_refinement(
        _fixture(),
        tmp_path / "out",
        adapter=_Adapter(297.0022, 210.0072),  # type: ignore[arg-type]
        rasterizer=_Rasterizer(),
    )

    assert artifact.svg_view_box_mm[2:] == pytest.approx((297.0022, 210.0072))


def test_a1_render_uses_deterministic_four_region_tiling(tmp_path: Path) -> None:
    schematic = _fixture_with_paper(tmp_path, "A1")
    first = render_schematic_for_refinement(
        schematic,
        tmp_path / "first",
        adapter=_Adapter(841.0, 594.0),  # type: ignore[arg-type]
        rasterizer=_Rasterizer(),
    )
    second = render_schematic_for_refinement(
        schematic,
        tmp_path / "second",
        adapter=_Adapter(841.0, 594.0),  # type: ignore[arg-type]
        rasterizer=_Rasterizer(),
    )

    assert [region.region_id for region in first.review_regions] == [
        "r00-c00",
        "r00-c01",
        "r01-c00",
        "r01-c01",
    ]
    assert [region.image_index for region in first.review_regions] == [0, 1, 2, 3]
    assert [region.view_box_mm for region in first.review_regions] == [
        region.view_box_mm for region in second.review_regions
    ]
    assert [region.png_hash for region in first.review_regions] == [
        region.png_hash for region in second.review_regions
    ]
    assert all(
        region.width_px <= MAX_REVIEW_REGION_DIMENSION_PX
        and region.height_px <= MAX_REVIEW_REGION_DIMENSION_PX
        for region in first.review_regions
    )
    assert all(
        region.pixels_per_mm_x >= REVIEW_PIXELS_PER_MM - 0.01
        and region.pixels_per_mm_y >= REVIEW_PIXELS_PER_MM - 0.01
        for region in first.review_regions
    )

    top_left, top_right, bottom_left, bottom_right = first.review_regions
    assert top_left.view_box_mm[0:2] == (0.0, 0.0)
    assert top_right.view_box_mm[0] < top_left.view_box_mm[0] + top_left.view_box_mm[2]
    assert bottom_left.view_box_mm[1] < top_left.view_box_mm[1] + top_left.view_box_mm[3]
    assert top_right.view_box_mm[0] + top_right.view_box_mm[2] == pytest.approx(841.0)
    assert bottom_right.view_box_mm[1] + bottom_right.view_box_mm[3] == pytest.approx(594.0)
    assert len(first.review_image_paths) == 4


@pytest.mark.parametrize(
    ("paper", "width_mm", "height_mm", "expected_regions"),
    [
        ("A3", 420.0, 297.0, 1),
        ("A2", 594.0, 420.0, 2),
    ],
)
def test_readability_threshold_keeps_a3_whole_and_tiles_a2(
    tmp_path: Path,
    paper: str,
    width_mm: float,
    height_mm: float,
    expected_regions: int,
) -> None:
    schematic = _fixture_with_paper(tmp_path, paper)

    artifact = render_schematic_for_refinement(
        schematic,
        tmp_path / "out",
        adapter=_Adapter(width_mm, height_mm),  # type: ignore[arg-type]
        rasterizer=_Rasterizer(),
    )

    assert len(artifact.review_regions) == expected_regions


def test_a0_render_fails_closed_instead_of_shrinking_below_readable_scale(
    tmp_path: Path,
) -> None:
    schematic = _fixture_with_paper(tmp_path, "A0")

    with pytest.raises(UserError, match="too many readable") as exc_info:
        render_schematic_for_refinement(
            schematic,
            tmp_path / "out",
            adapter=_Adapter(1189.0, 841.0),  # type: ignore[arg-type]
            rasterizer=_Rasterizer(),
        )

    assert exc_info.value.code == "REFINEMENT_RENDER_TOO_LARGE"
    assert exc_info.value.details["required_regions"] == 6
    assert exc_info.value.details["max_regions"] == 4
