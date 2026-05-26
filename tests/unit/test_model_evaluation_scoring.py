from __future__ import annotations

from kicad_pcb.corpus.layout_features import LayoutFeatures
from kicad_pcb.evaluation.scoring import score_intrinsic_quality


def _features_payload(**overrides: object) -> dict[str, object]:
    intrinsic_lints = overrides.pop("intrinsic_lints", [])
    symbols = int(overrides.pop("symbols", 4))
    non_power_symbols = int(overrides.pop("non_power_symbols", 3))
    power_symbols = int(overrides.pop("power_symbols", 1))
    distinct_x_columns = int(overrides.pop("distinct_x_columns", 3))
    wire_stub_ratio = float(overrides.pop("wire_stub_ratio", 0.1))
    local_label_count = int(overrides.pop("local_label_count", 1))
    global_label_count = int(overrides.pop("global_label_count", 0))
    return {
        "schema_version": "1.0",
        "source": {"fixture_id": "fixture-1", "file": "source.kicad_sch"},
        "counts": {
            "symbols": symbols,
            "non_power_symbols": non_power_symbols,
            "power_symbols": power_symbols,
            "wires": 3,
            "labels": local_label_count,
            "global_labels": global_label_count,
            "junctions": 1,
            "no_connects": 0,
        },
        "symbols": {},
        "role_counts": {"passive": 2, "major_ic": 1},
        "net_label_strategy": {
            "local_label_count": local_label_count,
            "global_label_count": global_label_count,
            "power_symbol_count": power_symbols,
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
        "relative_positions": [],
        "intrinsic_lints": intrinsic_lints,
    }


def test_score_intrinsic_quality_rewards_clean_generated_layout() -> None:
    features = LayoutFeatures.model_validate(_features_payload())

    report = score_intrinsic_quality(features)

    assert report.score > 90.0
    assert report.reasons == ()


def test_score_intrinsic_quality_penalizes_empty_crowded_layout() -> None:
    features = LayoutFeatures.model_validate(
        _features_payload(
            symbols=0,
            non_power_symbols=1,
            power_symbols=0,
            distinct_x_columns=1,
            wire_stub_ratio=0.95,
            local_label_count=8,
            global_label_count=4,
            intrinsic_lints=[{"rule": "crowding", "severity": "warning"}],
        )
    )

    report = score_intrinsic_quality(features)

    assert report.score < 45.0
    assert any(reason.startswith("validity:") for reason in report.reasons)
    assert any(reason.startswith("routing_simplicity:") for reason in report.reasons)
