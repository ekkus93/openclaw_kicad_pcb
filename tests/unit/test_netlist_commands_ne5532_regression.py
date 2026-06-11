"""NE5532 fixture regression tests: route, position, and label quality."""

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
from kicad_pcb.layout import GRID_COL_MM
from kicad_pcb.lint.helpers import _collect_wire_segments
from kicad_pcb.sch_doc import SchematicDoc
from kicad_pcb.sexpr.nodes import AtomNode, ListNode, StringNode
from kicad_pcb.sexpr.utils import find_first, walk
from tests import NE5532_HEADPHONE_REVIEW_FIXTURE, SYMBOLS_FIXTURE_DIR

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


def _symbol_positions_by_id(doc: SchematicDoc, symbol_id: str) -> dict[str, tuple[float, float]]:
    positions: dict[str, tuple[float, float]] = {}
    for symbol in doc.list_symbols():
        ref = symbol["ref"]
        current_symbol_id = symbol["symbol_id"]
        x = symbol["x"]
        y = symbol["y"]
        if (
            isinstance(ref, str)
            and isinstance(current_symbol_id, str)
            and current_symbol_id == symbol_id
            and isinstance(x, float)
            and isinstance(y, float)
        ):
            positions[ref] = (x, y)
    return positions


def _symbol_angles(doc: SchematicDoc) -> dict[str, int]:
    angles: dict[str, int] = {}
    for node in doc.root.items:
        if not isinstance(node, ListNode) or node.key != "symbol":
            continue

        ref: str | None = None
        angle: int | None = None
        for child in node.items:
            if not isinstance(child, ListNode):
                continue
            if child.key == "property" and len(child.items) >= 3:
                name_node = child.items[1]
                value_node = child.items[2]
                if (
                    isinstance(name_node, StringNode)
                    and name_node.value == "Reference"
                    and isinstance(value_node, StringNode)
                ):
                    ref = value_node.value
            elif (
                child.key == "at" and len(child.items) >= 4 and isinstance(child.items[3], AtomNode)
            ):
                angle = int(float(child.items[3].value))

        if ref is not None and angle is not None:
            angles[ref] = angle

    return angles


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


def _get_lib_symbol_ids(doc: SchematicDoc) -> list[str]:
    """Return sorted list of symbol IDs embedded in (lib_symbols)."""
    lib_syms = find_first(doc.root, "lib_symbols")
    if lib_syms is None:
        return []
    ids: list[str] = []
    for item in lib_syms.items:
        if (
            isinstance(item, ListNode)
            and item.key == "symbol"
            and len(item.items) >= 2
            and isinstance(item.items[1], StringNode)
        ):
            ids.append(item.items[1].value)
    return sorted(ids)


@_skip_no_real_ne5532_fixture_symbols
def test_new_from_real_ne5532_fixture_keeps_stage_handoff_on_main_signal_band(
    tmp_path: Path,
) -> None:
    result = cmd_new_from_netlist(
        Namespace(
            name="RealNe5532StageBand",
            out_dir=str(tmp_path),
            description="",
            netlist=str(_REAL_NE5532_REVIEW_NETLIST),
            symbols_dir=str(_REAL_NE5532_SYMBOLS),
            mode="internal",
        )
    )

    managed_doc = SchematicDoc.load(result.managed_schematic_path)
    positions = _symbol_positions(managed_doc)

    assert positions["C6"][1] == pytest.approx(positions["U1A"][1]), (
        "The stage handoff bridge should stay on the main gain-to-buffer row: "
        + ", ".join(f"{ref}.y={positions[ref][1]:.2f}" for ref in ("U1A", "C6", "U1B"))
    )
    assert positions["R5"][1] == pytest.approx(positions["U1B"][1] + 7.62), (
        "The local buffer shunt should hang one row below the U1B input node: "
        + ", ".join(f"{ref}.y={positions[ref][1]:.2f}" for ref in ("C6", "R5", "U1B"))
    )
    assert positions["U1A"][0] <= positions["C6"][0] == positions["R5"][0] < positions["U1B"][0], (
        "The bridge-plus-shunt input node should remain between the gain "
        "stage and the buffer stage: "
        + ", ".join(f"{ref}.x={positions[ref][0]:.2f}" for ref in ("U1A", "C6", "R5", "U1B"))
    )


@_skip_no_real_ne5532_fixture_symbols
def test_new_from_real_ne5532_fixture_keeps_u1b_buffer_row_short_and_obvious(
    tmp_path: Path,
) -> None:
    result = cmd_new_from_netlist(
        Namespace(
            name="RealNe5532BufferRow",
            out_dir=str(tmp_path),
            description="",
            netlist=str(_REAL_NE5532_REVIEW_NETLIST),
            symbols_dir=str(_REAL_NE5532_SYMBOLS),
            mode="internal",
        )
    )

    managed_doc = SchematicDoc.load(result.managed_schematic_path)
    positions = _symbol_positions(managed_doc)

    assert positions["C6"][1] == pytest.approx(positions["U1B"][1]), (
        "The incoming handoff bridge should stay on the U1B stage row: "
        + ", ".join(f"{ref}.y={positions[ref][1]:.2f}" for ref in ("C6", "U1B", "R6"))
    )
    assert positions["R5"][1] == pytest.approx(positions["U1B"][1] + 7.62), (
        "The local shunt should sit below the short U1B buffer row instead of flattening onto it: "
        + ", ".join(f"{ref}.y={positions[ref][1]:.2f}" for ref in ("C6", "R5", "U1B", "R6"))
    )
    assert positions["C6"][0] == positions["R5"][0] < positions["U1B"][0] < positions["R6"][0], (
        "U1B should still read left-to-right from the input node into direct output support: "
        + ", ".join(f"{ref}.x={positions[ref][0]:.2f}" for ref in ("C6", "R5", "U1B", "R6"))
    )
    assert positions["R6"][0] - positions["U1B"][0] <= 30.48 + 1e-6, (
        "The direct U1B output element should stay close so the unity loop is visually obvious: "
        f"U1B.x={positions['U1B'][0]:.2f}, R6.x={positions['R6'][0]:.2f}"
    )


@_skip_no_real_ne5532_fixture_symbols
def test_new_from_real_ne5532_fixture_draws_u1b_feedback_as_compact_local_loop(
    tmp_path: Path,
) -> None:
    result = cmd_new_from_netlist(
        Namespace(
            name="RealNe5532BufferFeedbackLoop",
            out_dir=str(tmp_path),
            description="",
            netlist=str(_REAL_NE5532_REVIEW_NETLIST),
            symbols_dir=str(_REAL_NE5532_SYMBOLS),
            mode="internal",
            heuristic_profile="analog_audio",
        )
    )

    managed_doc = SchematicDoc.load(result.managed_schematic_path)
    positions = _symbol_positions(managed_doc)
    segments = _collect_wire_segments(managed_doc.root.items)

    u1b_x, u1b_y = positions["U1B"]
    r6_x, _r6_y = positions["R6"]

    horizontal_candidates: list[tuple[float, float, float]] = []
    vertical_segments: list[tuple[float, float, float]] = []
    for x1, y1, x2, y2 in segments:
        if math.isclose(y1, y2, abs_tol=0.05):
            x_min = round(min(x1, x2), 2)
            x_max = round(max(x1, x2), 2)
            y = round(y1, 2)
            if (
                x_min >= round(u1b_x, 2)
                and x_max <= round(r6_x, 2)
                and (x_max - x_min) <= 20.32
                and abs(y - round(u1b_y, 2)) <= 5.08
            ):
                horizontal_candidates.append((x_min, x_max, y))
        elif math.isclose(x1, x2, abs_tol=0.05):
            x = round(x1, 2)
            y_min = round(min(y1, y2), 2)
            y_max = round(max(y1, y2), 2)
            vertical_segments.append((x, y_min, y_max))

    matching_loop = None
    for x_min, x_max, y in horizontal_candidates:
        has_vertical_return = any(
            math.isclose(vertical_x, x_max, abs_tol=0.05)
            and vertical_y_min <= min(y, round(u1b_y, 2)) + 0.05
            and vertical_y_max >= max(y, round(u1b_y, 2)) - 0.05
            for vertical_x, vertical_y_min, vertical_y_max in vertical_segments
        )
        has_branch_to_r6 = any(
            math.isclose(seg_y1, seg_y2, abs_tol=0.05)
            and math.isclose(min(seg_x1, seg_x2), x_max, abs_tol=0.05)
            and max(seg_x1, seg_x2) > x_max + 5.0
            for seg_x1, seg_y1, seg_x2, seg_y2 in segments
        )
        if has_vertical_return and has_branch_to_r6:
            matching_loop = (x_min, x_max, y)
            break

    assert matching_loop is not None, (
        "The U1B output-to-inverting feedback should draw as a compact local "
        "jog before the R6 branch: "
        f"U1B={positions['U1B']}, R6={positions['R6']}, segments={segments}"
    )


@_skip_no_real_ne5532_fixture_symbols
def test_new_from_real_ne5532_fixture_shapes_u1b_input_as_bridge_plus_shunt_node(
    tmp_path: Path,
) -> None:
    result = cmd_new_from_netlist(
        Namespace(
            name="RealNe5532BufferInputNode",
            out_dir=str(tmp_path),
            description="",
            netlist=str(_REAL_NE5532_REVIEW_NETLIST),
            symbols_dir=str(_REAL_NE5532_SYMBOLS),
            mode="internal",
        )
    )

    managed_doc = SchematicDoc.load(result.managed_schematic_path)
    positions = _symbol_positions(managed_doc)

    u1b_x, u1b_y = positions["U1B"]
    c6_x, c6_y = positions["C6"]
    r5_x, r5_y = positions["R5"]

    assert c6_x == pytest.approx(r5_x), (
        "The U1B handoff bridge and shunt should share one input-node column: "
        f"C6={positions['C6']}, R5={positions['R5']}, U1B={positions['U1B']}"
    )
    assert u1b_x - c6_x == pytest.approx(GRID_COL_MM / 2.0), (
        "The U1B input-node column should sit midway between the handoff and the buffer stage: "
        f"C6.x={c6_x:.2f}, U1B.x={u1b_x:.2f}"
    )
    assert c6_y == pytest.approx(u1b_y), (
        f"The incoming handoff should stay on the U1B stage row: C6.y={c6_y:.2f}, U1B.y={u1b_y:.2f}"
    )
    assert r5_y == pytest.approx(u1b_y + 7.62), (
        "The local shunt support should hang one row below the U1B input node: "
        f"R5.y={r5_y:.2f}, U1B.y={u1b_y:.2f}"
    )


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
        {"compact_local_ground_cluster": ["GND"]},
        {
            "compact_local_decoupling_cluster": ["VPLUS15"],
            "compact_local_ground_cluster": ["GND"],
        },
    )
    assert digital_overrides == {}


@_skip_no_real_ne5532_fixture_symbols
def test_real_ne5532_power_profile_debug_dump_surfaces_ground_cluster_diff(
    tmp_path: Path,
) -> None:
    power_result = cmd_new_from_netlist(
        Namespace(
            name="RealNe5532PowerProfile",
            out_dir=str(tmp_path),
            description="",
            netlist=str(_REAL_NE5532_REVIEW_NETLIST),
            symbols_dir=str(_REAL_NE5532_SYMBOLS),
            mode="internal",
            heuristic_profile="power_supply",
            debug_dump=str(tmp_path / "power_supply_debug.json"),
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

    power_dump = json.loads(cast(Path, power_result.debug_dump_path).read_text(encoding="utf-8"))
    digital_dump = json.loads(
        cast(Path, digital_result.debug_dump_path).read_text(encoding="utf-8")
    )
    power_counts, power_overrides = _summarize_route_choices(cast(dict[str, object], power_dump))
    digital_counts, digital_overrides = _summarize_route_choices(
        cast(dict[str, object], digital_dump)
    )

    assert power_dump["heuristic_profile_name"] == "power_supply"
    assert digital_dump["heuristic_profile_name"] == "generic_digital"
    assert power_dump["net_classification"] == digital_dump["net_classification"]
    assert power_counts == digital_counts
    assert power_dump["routing_heuristic_policy"] == {
        "enable_compact_local_ground_clusters": True,
        "enable_compact_output_tails": False,
    }
    assert digital_dump["routing_heuristic_policy"] == {
        "enable_compact_local_ground_clusters": False,
        "enable_compact_output_tails": False,
    }
    assert power_overrides in ({}, {"compact_local_ground_cluster": ["GND"]})
    assert digital_overrides == {}


@_skip_no_real_ne5532_fixture_symbols
def test_real_ne5532_fixture_raw_graphviz_positions_preserve_stage_order(
    tmp_path: Path,
) -> None:
    result = cmd_new_from_netlist(
        Namespace(
            name="RealNe5532RawPlacementOrder",
            out_dir=str(tmp_path),
            description="",
            netlist=str(_REAL_NE5532_REVIEW_NETLIST),
            symbols_dir=str(_REAL_NE5532_SYMBOLS),
            mode="internal",
            heuristic_profile="analog_audio",
            debug_dump=str(tmp_path / "real_ne5532_raw_stage_order.json"),
        )
    )

    debug_dump = json.loads(cast(Path, result.debug_dump_path).read_text(encoding="utf-8"))
    raw_positions = cast(dict[str, dict[str, float]], debug_dump["raw_graphviz_positions"])

    stage_chain_x = [
        raw_positions["J1"]["x"],
        raw_positions["RV1"]["x"],
        raw_positions["U1A"]["x"],
        raw_positions["C6"]["x"],
        raw_positions["U1B"]["x"],
        raw_positions["R6"]["x"],
        raw_positions["J2"]["x"],
    ]

    assert stage_chain_x == sorted(stage_chain_x)
    assert raw_positions["J1"]["x"] < raw_positions["U1A"]["x"] < raw_positions["J2"]["x"]
    assert raw_positions["C6"]["x"] <= raw_positions["U1B"]["x"] <= raw_positions["R6"]["x"]


@_skip_no_real_ne5532_fixture_symbols
def test_new_from_real_ne5532_fixture_important_label_mode_surfaces_stage_seams(
    tmp_path: Path,
) -> None:
    result = cmd_new_from_netlist(
        Namespace(
            name="RealNe5532ImportantLabels",
            out_dir=str(tmp_path),
            description="",
            netlist=str(_REAL_NE5532_REVIEW_NETLIST),
            symbols_dir=str(_REAL_NE5532_SYMBOLS),
            mode="internal",
            label_mode="always-show-important-labels",
        )
    )

    managed_doc = SchematicDoc.load(result.managed_schematic_path)
    label_names: set[str] = set()
    for node in walk(managed_doc.root):
        if (
            isinstance(node, ListNode)
            and node.key == "label"
            and len(node.items) >= 2  # noqa: PLR2004
            and isinstance(node.items[1], StringNode)
        ):
            label_names.add(node.items[1].value)

    assert {
        "VOL_L_OUT",
        "OUT_L_STAGE1",
        "BUF_L_IN",
        "HP_L_OUT",
    } <= label_names
    assert "OUT_L_STAGE2_RAW" not in label_names
    assert "AFTER_R6" not in label_names
    assert "U1A_INV" not in label_names
