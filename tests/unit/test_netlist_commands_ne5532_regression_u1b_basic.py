"""NE5532 regression tests — U1B output / later tests."""

from __future__ import annotations

import json
import math
from argparse import Namespace
from collections import defaultdict
from pathlib import Path
from typing import cast

import pytest

from kicad_pcb.commands.netlist import (
    cmd_new_from_netlist,
)
from kicad_pcb.lint.helpers import _collect_wire_segments
from kicad_pcb.sch_doc import SchematicDoc
from tests import NE5532_HEADPHONE_REVIEW_FIXTURE, SYMBOLS_FIXTURE_DIR

pytestmark = pytest.mark.unit

_KICAD_SYSTEM_SYMBOLS = Path("/usr/share/kicad/symbols")
_REAL_NE5532_SYMBOLS = SYMBOLS_FIXTURE_DIR
_REAL_NE5532_REVIEW_NETLIST = NE5532_HEADPHONE_REVIEW_FIXTURE.netlist_path

_skip_no_system_symbols = pytest.mark.skipif(
    not (_KICAD_SYSTEM_SYMBOLS / "Amplifier_Operational.kicad_sym").exists(),
    reason="KiCad system symbol libraries not installed at /usr/share/kicad/symbols",
)

_skip_no_real_ne5532_fixture_symbols = pytest.mark.skipif(
    not all(
        (
            _REAL_NE5532_SYMBOLS / "Amplifier_Operational.kicad_sym",
            _REAL_NE5532_SYMBOLS / "Connector.kicad_sym",
            _REAL_NE5532_SYMBOLS / "Device.kicad_sym",
            _REAL_NE5532_SYMBOLS / "Connector_Generic.kicad_sym",
        )
    ),
    reason="Portable NE5532 regression symbol fixtures are missing",
)


def _summarize_route_choices(
    dump: dict[str, object],
) -> tuple[dict[str, int], dict[str, list[str]]]:
    counts: dict[str, int] = {}
    overrides: dict[str, list[str]] = {}
    route_choices = cast(list[dict[str, object]], dump["final_route_choices"])
    for choice in route_choices:
        strategy = cast(str, choice["strategy"])
        counts[strategy] = counts.get(strategy, 0) + 1
        heuristic_override = choice.get("heuristic_override")
        net_name = cast(str, choice["net_name"])
        if isinstance(heuristic_override, str):
            overrides.setdefault(heuristic_override, []).append(net_name)
    return counts, overrides


def _symbol_positions(doc: SchematicDoc) -> dict[str, tuple[float, float]]:
    positions: dict[str, tuple[float, float]] = {}
    for symbol in doc.list_symbols():
        ref = symbol["ref"]
        x = symbol["x"]
        y = symbol["y"]
        if isinstance(ref, str) and isinstance(x, float) and isinstance(y, float):
            positions[ref] = (x, y)
    return positions


def _distance_mm(left: tuple[float, float], right: tuple[float, float]) -> float:
    return math.hypot(left[0] - right[0], left[1] - right[1])


def _net_bound_refs(doc: SchematicDoc) -> dict[str, set[str]]:
    positions = _symbol_positions(doc)
    refs_by_net: dict[str, set[str]] = defaultdict(set)
    for binding in doc.extract_pin_label_bindings():
        net_name = binding["net_name"]
        ref = binding["ref"]
        if isinstance(net_name, str) and isinstance(ref, str) and ref in positions:
            refs_by_net[net_name].add(ref)
    return refs_by_net


def _max_ref_span_mm(
    positions: dict[str, tuple[float, float]],
    refs: set[str],
) -> float:
    ref_positions = [positions[ref] for ref in sorted(refs) if ref in positions]
    if len(ref_positions) < 2:
        return 0.0

    return max(
        _distance_mm(left, right)
        for index, left in enumerate(ref_positions)
        for right in ref_positions[index + 1 :]
    )


def _route_quality_metrics(doc: SchematicDoc) -> dict[str, float | dict[str, float]]:
    segments = _collect_wire_segments(doc.root.items)
    incident_orientations: dict[tuple[float, float], list[str]] = defaultdict(list)
    for x1, y1, x2, y2 in segments:
        orientation = "h" if round(y1, 2) == round(y2, 2) else "v"
        incident_orientations[(round(x1, 2), round(y1, 2))].append(orientation)
        incident_orientations[(round(x2, 2), round(y2, 2))].append(orientation)

    bend_count = sum(
        1
        for orientations in incident_orientations.values()
        if len(orientations) == 2 and set(orientations) == {"h", "v"}
    )
    junction_count = sum(
        1 for orientations in incident_orientations.values() if len(orientations) >= 3
    )

    positions = _symbol_positions(doc)
    refs_by_net = _net_bound_refs(doc)
    local_net_names = (
        "LEFT_IN",
        "IN_L_AC",
        "BUF_L_IN",
        "U1A_INV",
        "AFTER_R6",
        "HP_L_OUT",
    )
    local_net_spans = {
        net_name: _max_ref_span_mm(positions, refs_by_net.get(net_name, set()))
        for net_name in local_net_names
    }

    return {
        "wire_count": float(len(segments)),
        "bend_count": float(bend_count),
        "junction_count": float(junction_count),
        "avg_local_net_span": sum(local_net_spans.values()) / len(local_net_spans),
        "feedback_loop_max_span": _max_ref_span_mm(positions, refs_by_net.get("U1A_INV", set())),
        "local_net_spans": local_net_spans,
    }


# ---------------------------------------------------------------------------
# Phase 1 — warning suite
# ---------------------------------------------------------------------------


@_skip_no_real_ne5532_fixture_symbols
def test_new_from_real_ne5532_fixture_keeps_u1b_output_tail_compact_and_local(
    tmp_path: Path,
) -> None:
    result = cmd_new_from_netlist(
        Namespace(
            name="RealNe5532BufferTail",
            out_dir=str(tmp_path),
            description="",
            netlist=str(_REAL_NE5532_REVIEW_NETLIST),
            symbols_dir=str(_REAL_NE5532_SYMBOLS),
            mode="internal",
        )
    )

    managed_doc = SchematicDoc.load(result.managed_schematic_path)
    positions = _symbol_positions(managed_doc)

    tail_refs = ("C7", "R7", "J2")
    tail_ys = [positions[ref][1] for ref in tail_refs]

    assert max(tail_ys) - min(tail_ys) <= 7.62, (
        "The U1B output tail should read as one compact right-side chain: "
        + ", ".join(f"{ref}.y={positions[ref][1]:.2f}" for ref in tail_refs)
    )
    assert min(tail_ys) > positions["R6"][1], (
        "The output tail should stay below the fixed U1B buffer row: "
        + ", ".join(f"{ref}.y={positions[ref][1]:.2f}" for ref in ("R6", "C7", "R7", "J2"))
    )
    assert positions["R6"][0] < positions["C7"][0] <= positions["R7"][0] <= positions["J2"][0], (
        "The output tail should remain ordered to the right of R6: "
        + ", ".join(f"{ref}.x={positions[ref][0]:.2f}" for ref in ("R6", "C7", "R7", "J2"))
    )
    assert positions["J2"][0] - positions["R6"][0] <= 91.44, (
        "The U1B output tail should stay local instead of stretching far right: "
        f"R6.x={positions['R6'][0]:.2f}, J2.x={positions['J2'][0]:.2f}"
    )


@_skip_no_real_ne5532_fixture_symbols
def test_new_from_real_ne5532_fixture_keeps_route_quality_metrics_bounded(
    tmp_path: Path,
) -> None:
    result = cmd_new_from_netlist(
        Namespace(
            name="RealNe5532RouteQuality",
            out_dir=str(tmp_path),
            description="",
            netlist=str(_REAL_NE5532_REVIEW_NETLIST),
            symbols_dir=str(_REAL_NE5532_SYMBOLS),
            mode="internal",
        )
    )

    managed_doc = SchematicDoc.load(result.managed_schematic_path)
    metrics = _route_quality_metrics(managed_doc)
    local_net_spans = cast(dict[str, float], metrics["local_net_spans"])

    assert cast(float, metrics["wire_count"]) <= 150.0
    assert cast(float, metrics["bend_count"]) <= 80.0
    assert cast(float, metrics["junction_count"]) <= 25.0
    assert cast(float, metrics["avg_local_net_span"]) <= 55.0
    assert cast(float, metrics["feedback_loop_max_span"]) <= 55.0
    assert local_net_spans["LEFT_IN"] <= 35.0
    assert local_net_spans["IN_L_AC"] <= 40.0
    assert local_net_spans["BUF_L_IN"] <= 65.0
    assert local_net_spans["HP_L_OUT"] <= 65.0


@_skip_no_real_ne5532_fixture_symbols
def test_real_ne5532_fixture_profile_debug_dump_summary_diff(tmp_path: Path) -> None:
    analog_result = cmd_new_from_netlist(
        Namespace(
            name="RealNe5532AnalogProfile",
            out_dir=str(tmp_path),
            description="",
            netlist=str(_REAL_NE5532_REVIEW_NETLIST),
            symbols_dir=str(_REAL_NE5532_SYMBOLS),
            mode="internal",
            heuristic_profile="analog_audio",
            debug_dump=str(tmp_path / "analog_audio_debug.json"),
        )
    )
    digital_result = cmd_new_from_netlist(
        Namespace(
            name="RealNe5532DigitalProfile",
            out_dir=str(tmp_path),
            description="",
            netlist=str(_REAL_NE5532_REVIEW_NETLIST),
            symbols_dir=str(_REAL_NE5532_SYMBOLS),
            mode="internal",
            heuristic_profile="generic_digital",
            debug_dump=str(tmp_path / "generic_digital_debug.json"),
        )
    )

    analog_dump = json.loads(cast(Path, analog_result.debug_dump_path).read_text(encoding="utf-8"))
    digital_dump = json.loads(
        cast(Path, digital_result.debug_dump_path).read_text(encoding="utf-8")
    )
    analog_counts, analog_overrides = _summarize_route_choices(cast(dict[str, object], analog_dump))
    digital_counts, digital_overrides = _summarize_route_choices(
        cast(dict[str, object], digital_dump)
    )

    assert analog_dump["heuristic_profile_name"] == "analog_audio"
    assert digital_dump["heuristic_profile_name"] == "generic_digital"
    assert analog_counts != digital_counts
    assert analog_counts.get("shared_lane", 0) < digital_counts.get("shared_lane", 0)
    assert analog_counts.get("chain", 0) > digital_counts.get("chain", 0)
    assert analog_overrides.get("small_analog_local_routing") == [
        "BUF_L_IN",
        "HP_L_OUT",
        "OUT_L_STAGE1",
        "OUT_L_STAGE2_RAW",
        "U1A_INV",
        "VOL_L_OUT",
    ]
    profile_specific_overrides = {
        key: value for key, value in analog_overrides.items() if key != "small_analog_local_routing"
    }
    assert profile_specific_overrides in (
        {},
        {"compact_local_decoupling_cluster": ["VPLUS15"]},
        {"compact_local_ground_cluster": ["GND"]},
        {
            "compact_local_decoupling_cluster": ["VPLUS15"],
            "compact_local_ground_cluster": ["GND"],
        },
    )
    assert digital_overrides == {}
