from __future__ import annotations

import hashlib
import json
import math
import struct
import xml.etree.ElementTree as ET
import zlib
from pathlib import Path

import pytest
from kicad_pcb.evaluation.refinement_baseline import (
    RefinementBaselineCaptureRequest,
    capture_refinement_baseline,
    fixture_definition,
)

from kicad_pcb.adapters import KicadCliAdapter
from kicad_pcb.circuit_ir import CircuitIR
from kicad_pcb.electrical_equivalence import build_circuit_ir_fingerprint
from kicad_pcb.refinement import rendering
from kicad_pcb.runner import find_kicad_cli
from tests.conftest import requires_kicad

_FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures"
_EVALUATION_ROOT = _FIXTURE_ROOT / "refinement" / "evaluation_corpus"
_MODEL_CORPUS_ROOT = _FIXTURE_ROOT / "model_corpus"
_MANIFEST = json.loads((_EVALUATION_ROOT / "manifest.json").read_text(encoding="utf-8"))
_EXPECTATIONS = json.loads(
    (_EVALUATION_ROOT / "baseline_expectations.json").read_text(encoding="utf-8")
)
_EXPECTATIONS_BY_ID = {entry["fixture_id"]: entry for entry in _EXPECTATIONS["fixtures"]}


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + kind
        + payload
        + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
    )


def _png_bytes(width: int, height: int) -> bytes:
    header = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)
    row = b"\x00" + (b"\xff" * width)
    pixels = zlib.compress(row * height, level=9)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", header)
        + _png_chunk(b"IDAT", pixels)
        + _png_chunk(b"IEND", b"")
    )


def _deterministic_rasterize(svg_path: Path, png_path: Path) -> None:
    root = ET.parse(svg_path).getroot()
    raw_width = root.attrib.get("width", "")
    raw_height = root.attrib.get("height", "")
    if raw_width.endswith("px") and raw_height.endswith("px"):
        width = max(1, math.ceil(float(raw_width[:-2])))
        height = max(1, math.ceil(float(raw_height[:-2])))
    else:
        viewbox = tuple(float(value) for value in root.attrib["viewBox"].replace(",", " ").split())
        width = max(1, math.ceil(viewbox[2] * rendering.REVIEW_PIXELS_PER_MM))
        height = max(1, math.ceil(viewbox[3] * rendering.REVIEW_PIXELS_PER_MM))
    png_path.write_bytes(_png_bytes(width, height))


class _DeterministicRasterizer:
    def rasterize(self, svg_path: Path, png_path: Path) -> None:
        _deterministic_rasterize(svg_path, png_path)


_CASES = [pytest.param(entry, id=entry["fixture_id"]) for entry in _MANIFEST["fixtures"]]


@requires_kicad
@pytest.mark.parametrize("entry", _CASES)
def test_phase_n2_captures_real_kicad_baseline_for_every_fixture(
    entry: dict[str, object],
    home_tmp: Path,
) -> None:
    fixture_id = entry["fixture_id"]
    source_fixture_id = entry["source_fixture_id"]
    categories = entry["categories"]
    known_visual_defects = entry["known_visual_defects"]
    assert isinstance(fixture_id, str)
    assert isinstance(source_fixture_id, str)
    assert isinstance(categories, list)
    assert isinstance(known_visual_defects, list)
    assert all(isinstance(value, str) for value in categories)
    assert all(isinstance(value, str) for value in known_visual_defects)

    expectation = _EXPECTATIONS_BY_ID[fixture_id]
    source_dir = _MODEL_CORPUS_ROOT / source_fixture_id
    schematic = source_dir / "source_normalized.kicad_sch"
    authoritative = CircuitIR.load(source_dir / "circuit_ir.json")

    assert _hash(schematic) == expectation["source_schematic_sha256"]
    assert (
        build_circuit_ir_fingerprint(authoritative).sha256()
        == expectation["authoritative_fingerprint_sha256"]
    )

    output = capture_refinement_baseline(
        RefinementBaselineCaptureRequest(
            fixture=fixture_definition(
                fixture_id=fixture_id,
                source_fixture_id=source_fixture_id,
                categories=categories,
                known_visual_defects=known_visual_defects,
            ),
            authoritative_ir=authoritative,
            schematic=schematic,
            adapter=KicadCliAdapter(kicad_cli=find_kicad_cli()),
            rasterizer=_DeterministicRasterizer(),
        ),
        output_root=home_tmp / "phase-n2-baselines",
        work_root=home_tmp / "phase-n2-work",
    )

    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    electrical = json.loads((output / "electrical.json").read_text(encoding="utf-8"))
    metrics = json.loads((output / "metrics.json").read_text(encoding="utf-8"))

    assert manifest["fixture_id"] == fixture_id
    assert manifest["source_fixture_id"] == source_fixture_id
    assert manifest["categories"] == categories
    assert manifest["known_visual_defects"] == known_visual_defects
    assert manifest["electrical_status"] == "passed"
    assert electrical["status"] == "passed"
    assert metrics == expectation["metrics"]
    assert manifest["source_schematic_sha256"] == expectation["source_schematic_sha256"]
    assert manifest["authoritative_ir_sha256"] == expectation["authoritative_fingerprint_sha256"]
    assert manifest["render"]["view_box_mm"][2:] == pytest.approx(
        [expectation["page_mm"]["width"], expectation["page_mm"]["height"]],
        rel=0.0,
        abs=rendering.PAPER_DIMENSION_TOLERANCE_MM,
    )
    assert (output / "render" / "baseline.svg").is_file()
    assert (output / "render" / "baseline.svg").stat().st_size > 0
    assert (output / "render" / "baseline.png").is_file()
    assert manifest["render"]["svg_hash"] == _hash(output / "render" / "baseline.svg")
    assert manifest["render"]["png_hash"] == _hash(output / "render" / "baseline.png")
    assert str(home_tmp) not in json.dumps(manifest)
