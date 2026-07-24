from __future__ import annotations

import pytest

from kicad_pcb.corpus.layout_features import LayoutFeatures
from kicad_pcb.evaluation.similarity import (
    _quantize_rotation,
    _symbol_zone,
    compare_layout_similarity,
)


def _sym(
    ref: str,
    x: float,
    y: float,
    rotation: float = 0.0,
    *,
    is_power_symbol: bool = False,
) -> dict[str, object]:
    return {
        "ref": ref,
        "symbol_id": "Device:R",
        "value": "10k",
        "x": x,
        "y": y,
        "rotation": rotation,
        "unit": "1",
        "role_guess": "power_symbol" if is_power_symbol else "passive",
        "is_power_symbol": is_power_symbol,
        "is_connector": False,
        "is_passive": not is_power_symbol,
        "is_major_ic": False,
    }


def _features_payload(**overrides: object) -> dict[str, object]:
    role_counts = dict(overrides.pop("role_counts"))
    distinct_x_columns = int(overrides.pop("distinct_x_columns"))
    wire_stub_ratio = float(overrides.pop("wire_stub_ratio"))
    local_label_count = int(overrides.pop("local_label_count"))
    global_label_count = int(overrides.pop("global_label_count"))
    relative_positions = list(overrides.pop("relative_positions"))
    symbols: dict[str, object] = dict(overrides.pop("symbols") if "symbols" in overrides else {})
    return {
        "schema_version": "1.0",
        "source": {"fixture_id": "fixture-1", "file": "source.kicad_sch"},
        "counts": {
            "symbols": len(symbols),
            "non_power_symbols": sum(
                1 for value in symbols.values() if not value.get("is_power_symbol", False)
            ),
            "power_symbols": sum(
                1 for value in symbols.values() if value.get("is_power_symbol", False)
            ),
            "wires": 3,
            "labels": local_label_count,
            "global_labels": global_label_count,
            "junctions": 1,
            "no_connects": 0,
        },
        "symbols": symbols,
        "role_counts": role_counts,
        "net_label_strategy": {
            "local_label_count": local_label_count,
            "global_label_count": global_label_count,
            "power_symbol_count": 1,
        },
        "geometry": {
            "min_x": 10.0,
            "max_x": 100.0,
            "min_y": 10.0,
            "max_y": 80.0,
            "distinct_x_columns": distinct_x_columns,
            "average_symbol_spacing_mm": 25.0,
            "wire_stub_ratio": wire_stub_ratio,
        },
        "relative_positions": relative_positions,
        "intrinsic_lints": [],
    }


def _matching_payload(**extra: object) -> dict[str, object]:
    return _features_payload(
        role_counts={"connector": 1, "major_ic": 1, "passive": 2},
        distinct_x_columns=3,
        wire_stub_ratio=0.1,
        local_label_count=1,
        global_label_count=0,
        relative_positions=[{"a": "J1", "b": "U1", "relation": "left_of"}],
        **extra,
    )


def test_compare_layout_similarity_scores_matching_layouts_highly() -> None:
    source = LayoutFeatures.model_validate(_matching_payload())
    generated = LayoutFeatures.model_validate(_matching_payload())

    report = compare_layout_similarity(source, generated)

    assert report.score == 100.0
    assert report.reasons == ()
    assert report.sub_score_details["zone_positions"].applicable is False
    assert report.sub_score_details["orientation_match"].applicable is False


def test_compare_layout_similarity_reports_divergent_layouts() -> None:
    source = LayoutFeatures.model_validate(
        _features_payload(
            role_counts={"connector": 1, "major_ic": 1, "passive": 2},
            distinct_x_columns=4,
            wire_stub_ratio=0.1,
            local_label_count=1,
            global_label_count=0,
            relative_positions=[{"a": "J1", "b": "U1", "relation": "left_of"}],
        )
    )
    generated = LayoutFeatures.model_validate(
        _features_payload(
            role_counts={"major_ic": 1},
            distinct_x_columns=1,
            wire_stub_ratio=0.9,
            local_label_count=6,
            global_label_count=3,
            relative_positions=[{"a": "J1", "b": "U1", "relation": "above"}],
        )
    )

    report = compare_layout_similarity(source, generated)

    assert report.score < 50.0
    assert any(reason.startswith("role_counts:") for reason in report.reasons)
    assert any(reason.startswith("wire_stub_ratio:") for reason in report.reasons)


@pytest.mark.parametrize(
    ("x", "y", "expected"),
    [
        (10.0, 10.0, 0),
        (55.0, 55.0, 4),
        (100.0, 100.0, 8),
        (40.0, 40.0, 4),  # exact one-third boundary belongs to the middle zone
        (70.0, 70.0, 8),  # exact two-thirds boundary belongs to the final zone
        (-100.0, -100.0, 0),
    ],
)
def test_symbol_zone_boundaries(x: float, y: float, expected: int) -> None:
    bbox = (-100.0, 170.0, -100.0, 170.0) if x < 0 else (10.0, 100.0, 10.0, 100.0)
    assert _symbol_zone(x, y, bbox) == expected


def test_symbol_zone_degenerate_bounding_box_uses_center_zone() -> None:
    assert _symbol_zone(50.0, 50.0, (50.0, 50.0, 50.0, 50.0)) == 4


def test_zone_positions_are_translation_and_scale_invariant() -> None:
    source = LayoutFeatures.model_validate(
        _matching_payload(
            symbols={
                "R1": _sym("R1", 0.0, 0.0),
                "R2": _sym("R2", 50.0, 50.0),
                "R3": _sym("R3", 100.0, 100.0),
            }
        )
    )
    generated = LayoutFeatures.model_validate(
        _matching_payload(
            symbols={
                "R1": _sym("R1", 10.0, 10.0),
                "R2": _sym("R2", 110.0, 110.0),
                "R3": _sym("R3", 210.0, 210.0),
            }
        )
    )

    report = compare_layout_similarity(source, generated)

    assert report.sub_scores["zone_positions"] == 20.0
    assert report.sub_score_details["zone_positions"].applicable is True


def test_zone_positions_wrong_zones_score_zero() -> None:
    source = LayoutFeatures.model_validate(
        _matching_payload(
            symbols={
                "R1": _sym("R1", 0.0, 0.0),
                "R2": _sym("R2", 100.0, 100.0),
            }
        )
    )
    generated = LayoutFeatures.model_validate(
        _matching_payload(
            symbols={
                "R1": _sym("R1", 100.0, 100.0),
                "R2": _sym("R2", 0.0, 0.0),
            }
        )
    )

    report = compare_layout_similarity(source, generated)

    assert report.sub_scores["zone_positions"] == 0.0
    assert any(reason.startswith("zone_positions:") for reason in report.reasons)


def test_zone_positions_missing_refs_reduce_denominator_score() -> None:
    source = LayoutFeatures.model_validate(
        _matching_payload(
            symbols={
                "R1": _sym("R1", 0.0, 0.0),
                "R2": _sym("R2", 50.0, 50.0),
                "R3": _sym("R3", 100.0, 100.0),
            }
        )
    )
    generated = LayoutFeatures.model_validate(
        _matching_payload(
            symbols={
                "R1": _sym("R1", 0.0, 0.0),
                "R2": _sym("R2", 100.0, 100.0),
            }
        )
    )

    report = compare_layout_similarity(source, generated)

    assert report.sub_scores["zone_positions"] == pytest.approx(6.67)


def test_zone_positions_no_shared_refs_score_zero_not_full() -> None:
    source = LayoutFeatures.model_validate(
        _matching_payload(symbols={"R1": _sym("R1", 15.0, 15.0)})
    )
    generated = LayoutFeatures.model_validate(
        _matching_payload(symbols={"R2": _sym("R2", 95.0, 95.0)})
    )

    report = compare_layout_similarity(source, generated)

    assert report.sub_scores["zone_positions"] == 0.0
    assert "none of the expected" in report.sub_score_details["zone_positions"].reason


def test_zone_positions_power_only_is_not_applicable() -> None:
    source = LayoutFeatures.model_validate(
        _matching_payload(
            symbols={"#PWR01": _sym("#PWR01", 15.0, 15.0, is_power_symbol=True)}
        )
    )
    generated = LayoutFeatures.model_validate(
        _matching_payload(
            symbols={"#PWR01": _sym("#PWR01", 95.0, 95.0, is_power_symbol=True)}
        )
    )

    report = compare_layout_similarity(source, generated)

    assert report.sub_scores["zone_positions"] == 0.0
    assert report.sub_score_details["zone_positions"].applicable is False
    assert report.score == 100.0


@pytest.mark.parametrize(
    ("rotation", "expected"),
    [
        (-360.0, 0),
        (-315.0, 1),
        (-45.0, 0),
        (0.0, 0),
        (44.999, 0),
        (45.0, 1),
        (89.999, 1),
        (90.0, 1),
        (135.0, 2),
        (225.0, 3),
        (315.0, 0),
        (359.999, 0),
        (360.0, 0),
        (450.0, 1),
    ],
)
def test_quantize_rotation_boundaries(rotation: float, expected: int) -> None:
    assert _quantize_rotation(rotation) == expected


def test_orientation_match_identical_rotations_score_full() -> None:
    source = LayoutFeatures.model_validate(
        _matching_payload(symbols={"R1": _sym("R1", 50.0, 50.0, rotation=90.0)})
    )
    generated = LayoutFeatures.model_validate(
        _matching_payload(symbols={"R1": _sym("R1", 50.0, 50.0, rotation=90.0)})
    )

    report = compare_layout_similarity(source, generated)

    assert report.sub_scores["orientation_match"] == 15.0


def test_orientation_match_missing_refs_count_as_mismatches() -> None:
    source = LayoutFeatures.model_validate(
        _matching_payload(
            symbols={
                "R1": _sym("R1", 50.0, 50.0, rotation=0.0),
                "R2": _sym("R2", 70.0, 50.0, rotation=0.0),
            }
        )
    )
    generated = LayoutFeatures.model_validate(
        _matching_payload(symbols={"R1": _sym("R1", 50.0, 50.0, rotation=0.0)})
    )

    report = compare_layout_similarity(source, generated)

    assert report.sub_scores["orientation_match"] == 7.5


def test_orientation_match_no_shared_refs_score_zero_not_full() -> None:
    source = LayoutFeatures.model_validate(
        _matching_payload(symbols={"R1": _sym("R1", 50.0, 50.0, rotation=0.0)})
    )
    generated = LayoutFeatures.model_validate(
        _matching_payload(symbols={"R2": _sym("R2", 50.0, 50.0, rotation=90.0)})
    )

    report = compare_layout_similarity(source, generated)

    assert report.sub_scores["orientation_match"] == 0.0
    assert "none of the expected" in report.sub_score_details["orientation_match"].reason


def test_orientation_match_power_only_is_not_applicable() -> None:
    source = LayoutFeatures.model_validate(
        _matching_payload(
            symbols={
                "#PWR01": _sym(
                    "#PWR01", 50.0, 50.0, rotation=0.0, is_power_symbol=True
                )
            }
        )
    )
    generated = LayoutFeatures.model_validate(
        _matching_payload(
            symbols={
                "#PWR01": _sym(
                    "#PWR01", 50.0, 50.0, rotation=90.0, is_power_symbol=True
                )
            }
        )
    )

    report = compare_layout_similarity(source, generated)

    assert report.sub_scores["orientation_match"] == 0.0
    assert report.sub_score_details["orientation_match"].applicable is False
    assert report.score == 100.0


def test_completely_empty_source_has_no_applicable_metrics_and_scores_zero() -> None:
    source_payload = _features_payload(
        role_counts={},
        distinct_x_columns=0,
        wire_stub_ratio=0.0,
        local_label_count=0,
        global_label_count=0,
        relative_positions=[],
        symbols={},
    )
    source_payload["counts"]["wires"] = 0
    source_payload["net_label_strategy"]["power_symbol_count"] = 0
    generated_payload = dict(source_payload)
    source = LayoutFeatures.model_validate(source_payload)
    generated = LayoutFeatures.model_validate(generated_payload)

    report = compare_layout_similarity(source, generated)

    assert report.score == 0.0
    assert all(not detail.applicable for detail in report.sub_score_details.values())
    assert "similarity: no source layout metrics were applicable." in report.reasons
