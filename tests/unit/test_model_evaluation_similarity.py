from __future__ import annotations

from kicad_pcb.corpus.layout_features import LayoutFeatures
from kicad_pcb.evaluation.similarity import (
    _quantize_rotation,
    _symbol_zone,
    compare_layout_similarity,
)

# ── Fixture builder ───────────────────────────────────────────────────────────


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
    min_x: float = float(overrides.pop("min_x") if "min_x" in overrides else 10.0)
    max_x: float = float(overrides.pop("max_x") if "max_x" in overrides else 100.0)
    min_y: float = float(overrides.pop("min_y") if "min_y" in overrides else 10.0)
    max_y: float = float(overrides.pop("max_y") if "max_y" in overrides else 80.0)
    return {
        "schema_version": "1.0",
        "source": {"fixture_id": "fixture-1", "file": "source.kicad_sch"},
        "counts": {
            "symbols": 4,
            "non_power_symbols": 3,
            "power_symbols": 1,
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
            "min_x": min_x,
            "max_x": max_x,
            "min_y": min_y,
            "max_y": max_y,
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


# ── Existing tests (adjusted for redistributed weights, max still 100) ────────


def test_compare_layout_similarity_scores_matching_layouts_highly() -> None:
    source = LayoutFeatures.model_validate(_matching_payload())
    generated = LayoutFeatures.model_validate(_matching_payload())

    report = compare_layout_similarity(source, generated)

    assert report.score == 100.0
    assert report.reasons == ()


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


def test_compare_layout_similarity_does_not_fail_on_coordinate_only_differences() -> None:
    source = LayoutFeatures.model_validate(_matching_payload())
    generated = LayoutFeatures.model_validate(_matching_payload(min_x=400.0, max_x=700.0))

    report = compare_layout_similarity(source, generated)

    assert report.score == 100.0


# ── Zone position tests ───────────────────────────────────────────────────────


def test_symbol_zone_top_left() -> None:
    assert _symbol_zone(10.0, 10.0, (10.0, 100.0, 10.0, 100.0)) == 0


def test_symbol_zone_bottom_right() -> None:
    assert _symbol_zone(100.0, 100.0, (10.0, 100.0, 10.0, 100.0)) == 8


def test_symbol_zone_center() -> None:
    assert _symbol_zone(55.0, 55.0, (10.0, 100.0, 10.0, 100.0)) == 4


def test_symbol_zone_degenerate_bounding_box_uses_center_zone() -> None:
    # All symbols at the same point — degenerate box uses the middle zone (col=1, row=1 → 4)
    assert _symbol_zone(50.0, 50.0, (50.0, 50.0, 50.0, 50.0)) == 4


def test_zone_positions_matching_placements_score_full() -> None:
    # R1 in top-left of source, R1 in top-left of generated (different coordinates, same zone)
    source = LayoutFeatures.model_validate(
        _matching_payload(
            symbols={"R1": _sym("R1", 15.0, 15.0)},  # zone 0 of [10..100, 10..100]
            min_x=10.0,
            max_x=100.0,
            min_y=10.0,
            max_y=100.0,
        )
    )
    generated = LayoutFeatures.model_validate(
        _matching_payload(
            symbols={"R1": _sym("R1", 25.0, 25.0)},  # zone 0 of [20..200, 20..200]
            min_x=20.0,
            max_x=200.0,
            min_y=20.0,
            max_y=200.0,
        )
    )

    report = compare_layout_similarity(source, generated)

    assert report.sub_scores["zone_positions"] == 20.0
    assert not any(r.startswith("zone_positions:") for r in report.reasons)


def test_zone_positions_wrong_zone_scores_zero() -> None:
    # R1 in top-left of source, R1 in bottom-right of generated
    source = LayoutFeatures.model_validate(
        _matching_payload(
            symbols={"R1": _sym("R1", 15.0, 15.0)},  # zone 0
            min_x=10.0,
            max_x=100.0,
            min_y=10.0,
            max_y=100.0,
        )
    )
    generated = LayoutFeatures.model_validate(
        _matching_payload(
            symbols={"R1": _sym("R1", 95.0, 95.0)},  # zone 8
            min_x=10.0,
            max_x=100.0,
            min_y=10.0,
            max_y=100.0,
        )
    )

    report = compare_layout_similarity(source, generated)

    assert report.sub_scores["zone_positions"] == 0.0
    assert any(r.startswith("zone_positions:") for r in report.reasons)


def test_zone_positions_partial_match_scores_proportionally() -> None:
    # R1 matches zone, R2 does not
    source = LayoutFeatures.model_validate(
        _matching_payload(
            symbols={
                "R1": _sym("R1", 15.0, 15.0),  # zone 0
                "R2": _sym("R2", 95.0, 15.0),  # zone 2
            },
            min_x=10.0,
            max_x=100.0,
            min_y=10.0,
            max_y=100.0,
        )
    )
    generated = LayoutFeatures.model_validate(
        _matching_payload(
            symbols={
                "R1": _sym("R1", 15.0, 15.0),  # zone 0 — matches
                "R2": _sym("R2", 15.0, 15.0),  # zone 0 — wrong (source was zone 2)
            },
            min_x=10.0,
            max_x=100.0,
            min_y=10.0,
            max_y=100.0,
        )
    )

    report = compare_layout_similarity(source, generated)

    assert report.sub_scores["zone_positions"] == 10.0  # 1/2 matched × 20


def test_zone_positions_no_shared_refs_scores_full() -> None:
    source = LayoutFeatures.model_validate(
        _matching_payload(symbols={"R1": _sym("R1", 15.0, 15.0)})
    )
    generated = LayoutFeatures.model_validate(
        _matching_payload(symbols={"R2": _sym("R2", 95.0, 95.0)})
    )

    report = compare_layout_similarity(source, generated)

    assert report.sub_scores["zone_positions"] == 20.0


def test_zone_positions_power_symbols_excluded() -> None:
    # Power symbol in source and generated with mismatched zones — should not affect score
    source = LayoutFeatures.model_validate(
        _matching_payload(
            symbols={"#PWR01": _sym("#PWR01", 15.0, 15.0, is_power_symbol=True)},
            min_x=10.0,
            max_x=100.0,
            min_y=10.0,
            max_y=100.0,
        )
    )
    generated = LayoutFeatures.model_validate(
        _matching_payload(
            symbols={"#PWR01": _sym("#PWR01", 95.0, 95.0, is_power_symbol=True)},
            min_x=10.0,
            max_x=100.0,
            min_y=10.0,
            max_y=100.0,
        )
    )

    report = compare_layout_similarity(source, generated)

    assert report.sub_scores["zone_positions"] == 20.0


# ── Orientation match tests ───────────────────────────────────────────────────


def test_quantize_rotation_cardinal_angles() -> None:
    assert _quantize_rotation(0.0) == 0
    assert _quantize_rotation(90.0) == 1
    assert _quantize_rotation(180.0) == 2
    assert _quantize_rotation(270.0) == 3
    assert _quantize_rotation(360.0) == 0


def test_quantize_rotation_rounds_to_nearest_90() -> None:
    assert _quantize_rotation(44.0) == 0
    assert _quantize_rotation(46.0) == 1
    assert _quantize_rotation(135.0) == 2


def test_orientation_match_identical_rotations_score_full() -> None:
    source = LayoutFeatures.model_validate(
        _matching_payload(symbols={"R1": _sym("R1", 50.0, 50.0, rotation=90.0)})
    )
    generated = LayoutFeatures.model_validate(
        _matching_payload(symbols={"R1": _sym("R1", 50.0, 50.0, rotation=90.0)})
    )

    report = compare_layout_similarity(source, generated)

    assert report.sub_scores["orientation_match"] == 15.0
    assert not any(r.startswith("orientation_match:") for r in report.reasons)


def test_orientation_match_all_wrong_scores_zero() -> None:
    source = LayoutFeatures.model_validate(
        _matching_payload(symbols={"R1": _sym("R1", 50.0, 50.0, rotation=0.0)})
    )
    generated = LayoutFeatures.model_validate(
        _matching_payload(symbols={"R1": _sym("R1", 50.0, 50.0, rotation=90.0)})
    )

    report = compare_layout_similarity(source, generated)

    assert report.sub_scores["orientation_match"] == 0.0
    assert any(r.startswith("orientation_match:") for r in report.reasons)


def test_orientation_match_partial_scores_proportionally() -> None:
    source = LayoutFeatures.model_validate(
        _matching_payload(
            symbols={
                "R1": _sym("R1", 50.0, 50.0, rotation=0.0),
                "R2": _sym("R2", 70.0, 50.0, rotation=0.0),
            }
        )
    )
    generated = LayoutFeatures.model_validate(
        _matching_payload(
            symbols={
                "R1": _sym("R1", 50.0, 50.0, rotation=0.0),  # matches
                "R2": _sym("R2", 70.0, 50.0, rotation=90.0),  # wrong
            }
        )
    )

    report = compare_layout_similarity(source, generated)

    assert report.sub_scores["orientation_match"] == 7.5  # 1/2 × 15


def test_orientation_match_no_shared_refs_scores_full() -> None:
    source = LayoutFeatures.model_validate(
        _matching_payload(symbols={"R1": _sym("R1", 50.0, 50.0, rotation=0.0)})
    )
    generated = LayoutFeatures.model_validate(
        _matching_payload(symbols={"R2": _sym("R2", 50.0, 50.0, rotation=90.0)})
    )

    report = compare_layout_similarity(source, generated)

    assert report.sub_scores["orientation_match"] == 15.0


def test_orientation_match_treats_360_as_0() -> None:
    source = LayoutFeatures.model_validate(
        _matching_payload(symbols={"R1": _sym("R1", 50.0, 50.0, rotation=0.0)})
    )
    generated = LayoutFeatures.model_validate(
        _matching_payload(symbols={"R1": _sym("R1", 50.0, 50.0, rotation=360.0)})
    )

    report = compare_layout_similarity(source, generated)

    assert report.sub_scores["orientation_match"] == 15.0


def test_orientation_match_power_symbols_excluded() -> None:
    source = LayoutFeatures.model_validate(
        _matching_payload(
            symbols={"#PWR01": _sym("#PWR01", 50.0, 50.0, rotation=0.0, is_power_symbol=True)}
        )
    )
    generated = LayoutFeatures.model_validate(
        _matching_payload(
            symbols={"#PWR01": _sym("#PWR01", 50.0, 50.0, rotation=90.0, is_power_symbol=True)}
        )
    )

    report = compare_layout_similarity(source, generated)

    assert report.sub_scores["orientation_match"] == 15.0
