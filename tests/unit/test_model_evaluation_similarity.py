from __future__ import annotations

from kicad_pcb.corpus.layout_features import LayoutFeatures
from kicad_pcb.evaluation.similarity import compare_layout_similarity


def _features_payload(**overrides: object) -> dict[str, object]:
    role_counts = dict(overrides.pop("role_counts"))
    distinct_x_columns = int(overrides.pop("distinct_x_columns"))
    wire_stub_ratio = float(overrides.pop("wire_stub_ratio"))
    local_label_count = int(overrides.pop("local_label_count"))
    global_label_count = int(overrides.pop("global_label_count"))
    relative_positions = list(overrides.pop("relative_positions"))
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
        "symbols": {},
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


def test_compare_layout_similarity_scores_matching_layouts_highly() -> None:
    source = LayoutFeatures.model_validate(
        _features_payload(
            role_counts={"connector": 1, "major_ic": 1, "passive": 2},
            distinct_x_columns=3,
            wire_stub_ratio=0.1,
            local_label_count=1,
            global_label_count=0,
            relative_positions=[{"a": "J1", "b": "U1", "relation": "left_of"}],
        )
    )
    generated = LayoutFeatures.model_validate(
        _features_payload(
            role_counts={"connector": 1, "major_ic": 1, "passive": 2},
            distinct_x_columns=3,
            wire_stub_ratio=0.1,
            local_label_count=1,
            global_label_count=0,
            relative_positions=[{"a": "J1", "b": "U1", "relation": "left_of"}],
        )
    )

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
    source = LayoutFeatures.model_validate(
        _features_payload(
            role_counts={"connector": 1, "major_ic": 1, "passive": 2},
            distinct_x_columns=3,
            wire_stub_ratio=0.1,
            local_label_count=1,
            global_label_count=0,
            relative_positions=[{"a": "J1", "b": "U1", "relation": "left_of"}],
        )
    )
    generated_payload = _features_payload(
        role_counts={"connector": 1, "major_ic": 1, "passive": 2},
        distinct_x_columns=3,
        wire_stub_ratio=0.1,
        local_label_count=1,
        global_label_count=0,
        relative_positions=[{"a": "J1", "b": "U1", "relation": "left_of"}],
    )
    generated_payload["geometry"]["min_x"] = 400.0
    generated_payload["geometry"]["max_x"] = 700.0
    generated = LayoutFeatures.model_validate(generated_payload)

    report = compare_layout_similarity(source, generated)

    assert report.score == 100.0
