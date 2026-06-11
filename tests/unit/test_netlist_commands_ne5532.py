from __future__ import annotations

import json
import math
from argparse import Namespace
from collections import defaultdict
from pathlib import Path
from typing import cast

import pytest

from kicad_pcb.commands._sch_apply import MANAGED_SHEET_FILE, _transform_pin_at
from kicad_pcb.commands.netlist import (
    cmd_new_from_netlist,
)
from kicad_pcb.errors import ErrorCode, UserError
from kicad_pcb.layout import GRID_COL_MM
from kicad_pcb.lint.helpers import _collect_wire_segments
from kicad_pcb.sch_doc import SchematicDoc, read_lib_symbol_pin_at
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


def _check_circuit_fidelity(ir_data: dict, managed_doc: SchematicDoc) -> None:
    """Assert that *managed_doc* faithfully represents *ir_data*.

    Raises ``AssertionError`` with a descriptive message on the first mismatch.

    Parameters
    ----------
    ir_data:
        Parsed Circuit IR dict (``version``, ``components``, ``nets`` keys).
    managed_doc:
        The generated managed schematic loaded as a :class:`SchematicDoc`.
    """
    # --- component placement check ---
    placed_refs = {str(s["ref"]) for s in managed_doc.list_symbols()}
    for component in ir_data["components"]:
        ref = component["ref"]
        assert ref in placed_refs, (
            f"Component {ref!r} (symbol {component['symbol']!r}) "
            f"is missing from the generated schematic. "
            f"Placed refs: {sorted(placed_refs)}"
        )

    # --- net binding check ---
    # Build a lookup: (ref, pin) → net_name from the generated binding markers.
    binding_index: dict[tuple[str, str], str] = {
        (b["ref"], b["pin"]): b["net_name"] for b in managed_doc.extract_pin_label_bindings()
    }
    for net in ir_data["nets"]:
        net_name = net["name"]
        for pin_ref in net["pins"]:
            key = (pin_ref["ref"], pin_ref["pin"])
            assert key in binding_index, (
                f"No OpenClaw:bind= marker found for {pin_ref['ref']} pin {pin_ref['pin']!r} "
                f"(expected net {net_name!r}). "
                f"Bindings present: {sorted(binding_index.keys())}"
            )
            actual_net = binding_index[key]
            assert actual_net == net_name, (
                f"{pin_ref['ref']} pin {pin_ref['pin']!r}: "
                f"expected net {net_name!r} but schematic records {actual_net!r}"
            )


def test_circuit_fidelity_multi_component_testlib(tmp_path: Path) -> None:
    """Circuit fidelity: a 3-component, 4-net circuit with TestLib symbols.

    Uses R, OpAmp (flat), and DerivedOpAmp (extends OpAmp) together.
    Verifies that every component is placed and every net/pin binding is
    recorded correctly — including pins inherited by DerivedOpAmp.

    This test runs without system KiCad libraries and is always executed in CI.
    """
    ir_data = {
        "version": "1",
        "components": [
            {"ref": "R1", "symbol": "TestLib:R", "value": "10k"},
            {"ref": "R2", "symbol": "TestLib:R", "value": "22k"},
            {"ref": "U1", "symbol": "TestLib:DerivedOpAmp", "value": "DerivedOpAmp"},
        ],
        "nets": [
            # IN+ (pin 1 of DerivedOpAmp, inherited from OpAmp) through R1
            {"name": "IN_P", "pins": [{"ref": "U1", "pin": "1"}, {"ref": "R1", "pin": "1"}]},
            # IN- (pin 2, inherited) through R2
            {"name": "IN_N", "pins": [{"ref": "U1", "pin": "2"}, {"ref": "R2", "pin": "1"}]},
            # Feedback: OUT (pin 6, inherited) back to IN- via R2
            {"name": "OUT", "pins": [{"ref": "U1", "pin": "6"}, {"ref": "R2", "pin": "2"}]},
            # Input bias
            {"name": "GND", "pins": [{"ref": "R1", "pin": "2"}]},
        ],
    }
    fixtures_dir = Path(__file__).resolve().parent.parent / "fixtures" / "symbols"
    ir_path = tmp_path / "ir.json"
    ir_path.write_text(json.dumps(ir_data), encoding="utf-8")

    result = cmd_new_from_netlist(
        Namespace(
            name="FidelityTestLib",
            out_dir=str(tmp_path),
            description="",
            netlist=str(ir_path),
            symbols_dir=str(fixtures_dir),
            mode="internal",
        )
    )

    assert result.symbols_added == 3
    assert result.nets_applied == 4

    managed_doc = SchematicDoc.load(result.managed_schematic_path)
    _check_circuit_fidelity(ir_data, managed_doc)


def test_wires_connect_at_pin_endpoints(tmp_path: Path) -> None:  # noqa: PLR0912
    """P1: wires in the managed schematic start at the actual library pin endpoints.

    Before the P1 fix, _write_nets used arbitrary symbol-relative offsets
    (sym_x + 5.08, sym_y + 2.54*index) regardless of which pin was being
    wired.  After the fix, each wire must start at the exact (x, y) derived
    from the pin's ``(at X Y angle)`` in the library, translated by the
    symbol placement position.

    Circuit: R1 and R2 in series (VCC→R1→MID→R2→GND).
    TestLib:R pin positions:
      pin 1 at (at 0 0 0)   → endpoint at symbol_origin + (0, 0)
      pin 2 at (at 5.08 0 180) → endpoint at symbol_origin + (5.08, 0)
    """
    ir_data = {
        "version": "1",
        "components": [
            {"ref": "R1", "symbol": "TestLib:R", "value": "10k"},
            {"ref": "R2", "symbol": "TestLib:R", "value": "4.7k"},
        ],
        "nets": [
            {"name": "VCC", "pins": [{"ref": "R1", "pin": "1"}]},
            {"name": "MID", "pins": [{"ref": "R1", "pin": "2"}, {"ref": "R2", "pin": "1"}]},
            {"name": "GND", "pins": [{"ref": "R2", "pin": "2"}]},
        ],
    }
    fixtures_dir = Path(__file__).resolve().parent.parent / "fixtures" / "symbols"
    ir_path = tmp_path / "ir.json"
    ir_path.write_text(json.dumps(ir_data), encoding="utf-8")

    result = cmd_new_from_netlist(
        Namespace(
            name="WireTest",
            out_dir=str(tmp_path),
            description="",
            netlist=str(ir_path),
            symbols_dir=str(fixtures_dir),
            mode="internal",
        )
    )

    managed_doc = SchematicDoc.load(result.managed_schematic_path)

    # Collect all wire endpoints from the AST. Segment orientation is not
    # semantically meaningful after simplification, so either endpoint is valid.
    wire_endpoints: set[tuple[float, float]] = set()
    for node in walk(managed_doc.root):
        if not (isinstance(node, ListNode) and node.key == "wire"):
            continue
        pts = find_first(node, "pts")
        if pts is None:
            continue
        for xy in pts.items[1:3]:
            if isinstance(xy, ListNode) and xy.key == "xy" and len(xy.items) >= 3:
                try:
                    x = round(float(xy.items[1].value), 2)  # type: ignore[union-attr]
                    y = round(float(xy.items[2].value), 2)  # type: ignore[union-attr]
                    wire_endpoints.add((x, y))
                except (ValueError, AttributeError):
                    pass

    # Compute expected pin endpoints from the ACTUAL symbol positions in the
    # generated schematic using the same helper as generation.
    pin_at = read_lib_symbol_pin_at("TestLib", "R", symbols_dir=fixtures_dir)
    assert pin_at, "TestLib:R pin positions not found in fixture library"

    expected_endpoints: dict[tuple[str, str], tuple[float, float]] = {}
    for sym in managed_doc.list_symbols():
        ref = str(sym["ref"])
        sx, sy = cast(float, sym["x"]), cast(float, sym["y"])
        rotation = int(cast(float, sym.get("rotation", 0.0)))
        transformed_pin_at = _transform_pin_at(pin_at, sx, sy, rotation)
        for pin_num, (px, py, _pa) in transformed_pin_at.items():
            expected_endpoints[(ref, pin_num)] = (round(px, 2), round(py, 2))

    # Verify every expected pin endpoint is touched by a wire segment.
    # Skip power symbols (#PWR* refs) — they are placed at stub ends and do
    # not need outgoing wires of their own.
    missing: list[str] = []
    for (ref, pin), (ex, ey) in sorted(expected_endpoints.items()):
        if ref.startswith("#"):
            continue  # power symbol — no outgoing wire expected
        if (ex, ey) not in wire_endpoints:
            missing.append(f"{ref} pin {pin}: expected wire endpoint at ({ex}, {ey})")

    assert not missing, (
        "Wire(s) do not touch pin endpoints — wiring is disconnected:\n"
        + "\n".join(f"  {m}" for m in missing)
        + f"\nActual wire endpoints: {sorted(wire_endpoints)}"
    )


def test_direct_wiring_not_all_label_only(tmp_path: Path) -> None:
    """Router: a 2-pin net within routing range must be wired directly, not via label.

    A two-resistor voltage-divider (VCC→R1→MID→R2→GND) has three nets:
     - VCC  (power)  → gets a power:VCC symbol (Phase 3 strategy)
     - MID  (2-pin)  → R1-pin2 and R2-pin1 are adjacent-tier (tier distance=1)
                       and within the 200 mm manhattan cap; router must emit
                       an L-shaped wire, NOT a net label
     - GND  (power)  → gets a power:GND symbol (Phase 3 strategy)

    Assertions
    ----------
    1. No ``(label "MID" …)`` node exists in the managed schematic.
    2. At least 5 wire segments are present (4 pin stubs + ≥1 L-route bridge) —
       a direct-wire bridge was actually generated between R1 and R2.
    3. ``power:VCC`` and ``power:GND`` symbol instances exist (Phase 3) —
       power net routing uses symbols, not global labels.
    """
    ir_path = tmp_path / "divider.json"
    ir_path.write_text(
        json.dumps(
            {
                "version": "1",
                "components": [
                    {"ref": "R1", "symbol": "TestLib:R", "value": "10k"},
                    {"ref": "R2", "symbol": "TestLib:R", "value": "4.7k"},
                ],
                "nets": [
                    {"name": "VCC", "pins": [{"ref": "R1", "pin": "1"}]},
                    {
                        "name": "MID",
                        "pins": [
                            {"ref": "R1", "pin": "2"},
                            {"ref": "R2", "pin": "1"},
                        ],
                    },
                    {"name": "GND", "pins": [{"ref": "R2", "pin": "2"}]},
                ],
            }
        ),
        encoding="utf-8",
    )

    fixtures_dir = Path(__file__).resolve().parent.parent / "fixtures" / "symbols"
    result = cmd_new_from_netlist(
        Namespace(
            name="DirectWireTest",
            out_dir=str(tmp_path),
            description="",
            netlist=str(ir_path),
            symbols_dir=str(fixtures_dir),
            mode="internal",
        )
    )
    managed_doc = SchematicDoc.load(result.managed_schematic_path)

    # Collect all net-label text values from the schematic AST.
    label_names: set[str] = set()
    for node in walk(managed_doc.root):
        if (
            isinstance(node, ListNode)
            and node.key == "label"
            and len(node.items) >= 2  # noqa: PLR2004
            and isinstance(node.items[1], StringNode)
        ):
            label_names.add(node.items[1].value)

    # Assertion 1: MID must NOT appear as a label — it must be directly wired.
    assert "MID" not in label_names, (
        f"Net 'MID' found as a schematic label; expected direct wire routing. "
        f"All labels present: {sorted(label_names)}"
    )

    # Assertion 2: a direct-wire bridge between R1 and R2 must have been emitted.
    # For direct routing the router adds 2 stubs per MID pin + 1–2 L-route bridge
    # segments.  For only stub fallback it would have added a local net label for
    # MID (caught by assertion 1).  We verify that at least one bridge wire
    # exists in addition to the 4 pin-stub wires (VCC, GND, R1-pin2, R2-pin1).
    all_wire_segments: list[tuple[float, float, float, float]] = []
    for node in walk(managed_doc.root):
        if not (isinstance(node, ListNode) and node.key == "wire"):
            continue
        pts = find_first(node, "pts")
        if pts is None or len(pts.items) < 3:  # noqa: PLR2004
            continue
        xy1, xy2 = pts.items[1], pts.items[2]
        if isinstance(xy1, ListNode) and isinstance(xy2, ListNode):
            try:
                x1 = float(xy1.items[1].value)  # type: ignore[union-attr]
                y1 = float(xy1.items[2].value)  # type: ignore[union-attr]
                x2 = float(xy2.items[1].value)  # type: ignore[union-attr]
                y2 = float(xy2.items[2].value)  # type: ignore[union-attr]
                all_wire_segments.append((x1, y1, x2, y2))
            except (ValueError, AttributeError, IndexError):
                pass

    # 4 stub wires (VCC stub, GND stub, R1-pin2 stub, R2-pin1 stub) + at least
    # one L-route bridge = minimum 5 wire segments for a direct-wire routing.
    assert len(all_wire_segments) >= 5, (  # noqa: PLR2004
        f"Expected ≥5 wire segments for direct-wire routing; found {len(all_wire_segments)}. "
        "The router may not have emitted an L-route bridge between R1 and R2."
    )

    # Assertion 3 (Phase 3): Single-pin power nets must get power symbol nodes
    # (power:VCC / power:GND), not local labels or global labels.
    power_lib_ids: set[str] = set()
    for node in walk(managed_doc.root):
        if not (isinstance(node, ListNode) and node.key == "symbol"):
            continue
        lib_id_node = find_first(node, "lib_id")
        if (
            lib_id_node is not None
            and len(lib_id_node.items) >= 2  # noqa: PLR2004
            and isinstance(lib_id_node.items[1], StringNode)
        ):
            power_lib_ids.add(lib_id_node.items[1].value)
    assert "power:VCC" in power_lib_ids, (
        f"Expected power:VCC symbol for power net 'VCC'; lib_ids found: {sorted(power_lib_ids)}"
    )
    assert "power:GND" in power_lib_ids, (
        f"Expected power:GND symbol for power net 'GND'; lib_ids found: {sorted(power_lib_ids)}"
    )


@_skip_no_system_symbols
def test_ne5532_full_circuit_fidelity_with_system_libraries(tmp_path: Path) -> None:
    """Circuit fidelity: NE5532 op-amp circuit using real KiCad system libraries.

    NE5532 uses (extends "LM2904") in the KiCad library.  This test verifies
    the complete pipeline on a realistic circuit:

    - U1 NE5532 (dual op-amp, 8 pins, all inherited from LM2904)
    - R1-R4 Device:R

    The circuit exercises BOTH op-amp units inside U1:
      Unit A: pins 3 (IN+), 2 (IN-), 1 (OUT)
      Unit B: pins 5 (IN+), 6 (IN-), 7 (OUT)
      Power:  pins 8 (V+), 4 (V-)

    Fidelity assertions:
    1. The managed schematic places explicit KiCad units ``U1A``, ``U1B``, ``U1P``.
    2. All 8 nets have correct OpenClaw:bind= markers against those unit refs.
    3. NE5532 is embedded as a flat (non-extends) symbol — read_lib_symbol_def_flat
       merges LM2904's geometry into the NE5532 node so KiCad renders it correctly
       without needing a separate LM2904 entry in lib_symbols.
    """
    ir_data = {
        "version": "1",
        "components": [
            {"ref": "U1", "symbol": "Amplifier_Operational:NE5532", "value": "NE5532"},
            {"ref": "R1", "symbol": "Device:R", "value": "10k"},
            {"ref": "R2", "symbol": "Device:R", "value": "100k"},
            {"ref": "R3", "symbol": "Device:R", "value": "10k"},
            {"ref": "R4", "symbol": "Device:R", "value": "100k"},
        ],
        "nets": [
            # Power rails
            {"name": "VCC", "pins": [{"ref": "U1", "pin": "8"}]},
            {
                "name": "GND",
                "pins": [
                    {"ref": "U1", "pin": "4"},
                    {"ref": "R1", "pin": "2"},
                    {"ref": "R3", "pin": "2"},
                ],
            },
            # Unit A: inverting amplifier (pins 1, 2, 3)
            {"name": "IN_A", "pins": [{"ref": "U1", "pin": "3"}, {"ref": "R1", "pin": "1"}]},
            {"name": "IN_N_A", "pins": [{"ref": "U1", "pin": "2"}, {"ref": "R2", "pin": "1"}]},
            {"name": "OUT_A", "pins": [{"ref": "U1", "pin": "1"}, {"ref": "R2", "pin": "2"}]},
            # Unit B: inverting amplifier (pins 5, 6, 7)
            {"name": "IN_B", "pins": [{"ref": "U1", "pin": "5"}, {"ref": "R3", "pin": "1"}]},
            {"name": "IN_N_B", "pins": [{"ref": "U1", "pin": "6"}, {"ref": "R4", "pin": "1"}]},
            {"name": "OUT_B", "pins": [{"ref": "U1", "pin": "7"}, {"ref": "R4", "pin": "2"}]},
        ],
    }
    ir_path = tmp_path / "ir.json"
    ir_path.write_text(json.dumps(ir_data), encoding="utf-8")

    result = cmd_new_from_netlist(
        Namespace(
            name="NE5532Circuit",
            out_dir=str(tmp_path),
            description="",
            netlist=str(ir_path),
            symbols_dir=str(_KICAD_SYSTEM_SYMBOLS),
            mode="internal",
        )
    )

    assert result.symbols_added == 7
    assert result.nets_applied == 8

    managed_doc = SchematicDoc.load(result.managed_schematic_path)

    placed_symbols = {str(sym["ref"]): sym for sym in managed_doc.list_symbols()}
    assert {"R1", "R2", "R3", "R4", "U1A", "U1B", "U1P"} <= set(placed_symbols)
    assert placed_symbols["U1A"]["unit"] == "1"
    assert placed_symbols["U1B"]["unit"] == "2"
    assert placed_symbols["U1P"]["unit"] == "3"

    binding_index = {
        (binding["ref"], binding["pin"]): binding["net_name"]
        for binding in managed_doc.extract_pin_label_bindings()
    }
    assert binding_index[("U1P", "8")] == "VCC"
    assert binding_index[("U1P", "4")] == "GND"
    assert binding_index[("U1A", "3")] == "IN_A"
    assert binding_index[("U1A", "2")] == "IN_N_A"
    assert binding_index[("U1A", "1")] == "OUT_A"
    assert binding_index[("U1B", "5")] == "IN_B"
    assert binding_index[("U1B", "6")] == "IN_N_B"
    assert binding_index[("U1B", "7")] == "OUT_B"

    # Extends-chain specific: NE5532 is embedded as a flat symbol; LM2904 is NOT
    # embedded separately — its geometry was merged into the NE5532 node.
    embedded_ids = _get_lib_symbol_ids(managed_doc)
    assert "Amplifier_Operational:NE5532" in embedded_ids, (
        f"Derived symbol NE5532 missing from lib_symbols. Embedded: {embedded_ids}"
    )
    assert "Amplifier_Operational:LM2904" not in embedded_ids, (
        f"Base symbol LM2904 should not be embedded separately (geometry was merged "
        f"into NE5532 by read_lib_symbol_def_flat); got: {embedded_ids}"
    )

    # The embedded NE5532 node must be flat — no (extends ...) attribute.
    lib_syms = find_first(managed_doc.root, "lib_symbols")
    assert lib_syms is not None
    ne5532_node = next(
        (
            item
            for item in lib_syms.items
            if isinstance(item, ListNode)
            and item.key == "symbol"
            and len(item.items) >= 2
            and isinstance(item.items[1], StringNode)
            and item.items[1].value == "Amplifier_Operational:NE5532"
        ),
        None,
    )
    assert ne5532_node is not None
    extends_found = any(isinstance(c, ListNode) and c.key == "extends" for c in ne5532_node.items)
    assert not extends_found, (
        "NE5532 lib_symbols entry still contains (extends ...) — "
        "read_lib_symbol_def_flat should have removed it"
    )


# ---------------------------------------------------------------------------
# search-symbols / debug-symbol tests
# ---------------------------------------------------------------------------

from kicad_pcb.commands.search import cmd_debug_symbol, cmd_search_symbols  # noqa: E402
from kicad_pcb.results import DebugSymbolResult, SearchSymbolsResult  # noqa: E402

_TESTLIB_SYMBOLS_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "symbols"


def test_search_symbols_finds_exact_match() -> None:
    """Exact symbol name in query returns that symbol."""
    args = Namespace(query="OpAmp", symbols_dir=str(_TESTLIB_SYMBOLS_DIR), limit=20)
    result = cmd_search_symbols(args)
    assert isinstance(result, SearchSymbolsResult)
    ids = [m.symbol_id for m in result.matches]
    assert "TestLib:OpAmp" in ids


def test_search_symbols_finds_derived_symbol() -> None:
    """Searching 'DerivedOpAmp' finds both derived and possibly base symbol."""
    args = Namespace(query="DerivedOpAmp", symbols_dir=str(_TESTLIB_SYMBOLS_DIR), limit=20)
    result = cmd_search_symbols(args)
    ids = [m.symbol_id for m in result.matches]
    assert "TestLib:DerivedOpAmp" in ids


def test_search_symbols_no_match_returns_empty() -> None:
    """Query with no matches returns an empty matches tuple."""
    args = Namespace(query="zzz_no_such_thing_xyz", symbols_dir=str(_TESTLIB_SYMBOLS_DIR), limit=20)
    result = cmd_search_symbols(args)
    assert result.matches == ()


def test_search_symbols_empty_query_returns_empty() -> None:
    """Blank query returns empty results without error."""
    args = Namespace(query="   ", symbols_dir=str(_TESTLIB_SYMBOLS_DIR), limit=20)
    result = cmd_search_symbols(args)
    assert result.matches == ()


def test_search_symbols_result_fields() -> None:
    """SymbolMatch fields are correctly populated."""
    args = Namespace(query="R", symbols_dir=str(_TESTLIB_SYMBOLS_DIR), limit=20)
    result = cmd_search_symbols(args)
    r_match = next((m for m in result.matches if m.symbol_id == "TestLib:R"), None)
    assert r_match is not None, "TestLib:R not found in results"
    assert isinstance(r_match.pin_count, int)
    assert r_match.pin_count >= 0
    assert isinstance(r_match.description, str)


def test_search_symbols_limit_respected() -> None:
    """--limit caps the number of results returned."""
    args = Namespace(query="Op", symbols_dir=str(_TESTLIB_SYMBOLS_DIR), limit=1)
    result = cmd_search_symbols(args)
    assert len(result.matches) <= 1


def test_search_symbols_searched_dirs_reported() -> None:
    """symbols_dirs field reflects the directory that was searched."""
    args = Namespace(query="R", symbols_dir=str(_TESTLIB_SYMBOLS_DIR), limit=20)
    result = cmd_search_symbols(args)
    assert any(str(_TESTLIB_SYMBOLS_DIR) in d for d in result.symbols_dirs)


@_skip_no_system_symbols
def test_search_symbols_kicad9_renamed_symbols() -> None:
    """Verify KiCad 9 renamed symbols are discoverable via current library metadata."""
    args_cp = Namespace(
        query="polarized",
        symbols_dir=str(_KICAD_SYSTEM_SYMBOLS),
        limit=20,
    )
    result_cp = cmd_search_symbols(args_cp)
    ids_cp = [m.symbol_id for m in result_cp.matches]
    assert "Device:C_Polarized" in ids_cp, f"C_Polarized missing; got {ids_cp}"
    assert "Device:CP" not in ids_cp, "KiCad-8 legacy Device:CP should not appear in KiCad 9"

    args_pot = Namespace(
        query="potentiometer",
        symbols_dir=str(_KICAD_SYSTEM_SYMBOLS),
        limit=5,
    )
    result_pot = cmd_search_symbols(args_pot)
    ids_pot = [m.symbol_id for m in result_pot.matches]
    assert "Device:R_Potentiometer" in ids_pot, f"R_Potentiometer missing; got {ids_pot}"


# ---------------------------------------------------------------------------
# debug-symbol tests (P2)
# ---------------------------------------------------------------------------


def test_debug_symbol_standalone() -> None:
    """A symbol with its own pins reports correct list and no extends_base."""
    args = Namespace(symbol="TestLib:R", symbols_dir=str(_TESTLIB_SYMBOLS_DIR))
    result = cmd_debug_symbol(args)
    assert isinstance(result, DebugSymbolResult)
    assert result.symbol_id == "TestLib:R"
    assert result.extends_base is None
    assert result.pin_count == 2
    assert set(result.pin_numbers) == {"1", "2"}


def test_debug_symbol_extends() -> None:
    """An extends symbol reports extends_base and inherits parent pin count."""
    args = Namespace(symbol="TestLib:DerivedOpAmp", symbols_dir=str(_TESTLIB_SYMBOLS_DIR))
    result = cmd_debug_symbol(args)
    assert isinstance(result, DebugSymbolResult)
    assert result.symbol_id == "TestLib:DerivedOpAmp"
    assert result.extends_base == "TestLib:OpAmp"
    assert result.pin_count == 4  # inherited from OpAmp
    assert set(result.pin_numbers) == {"1", "2", "3", "6"}


def test_debug_symbol_not_found() -> None:
    """A non-existent symbol raises UserError with SYMBOL_NOT_FOUND code."""
    args = Namespace(symbol="TestLib:NoSuchSymbol", symbols_dir=str(_TESTLIB_SYMBOLS_DIR))
    with pytest.raises(UserError) as exc_info:
        cmd_debug_symbol(args)
    assert exc_info.value.code == ErrorCode.SYMBOL_NOT_FOUND


# ---------------------------------------------------------------------------
# Hierarchy path tests (fix: sheet_instances and symbol instances use parent UUID)
# ---------------------------------------------------------------------------


def test_update_managed_path_qualifies_sheet_instances(tmp_path: Path) -> None:
    """update_managed_path replaces '/' with '/{uuid}/' in sheet_instances."""
    sch_path = tmp_path / "managed.kicad_sch"
    sch_path.write_text(
        """(kicad_sch (version 20230121) (generator eeschema)
  (uuid "aaaa-bbbb-cccc")
  (paper "A4")
  (lib_symbols)
  (sheet_instances
    (path "/" (page "2"))
  )
)
""",
        encoding="utf-8",
    )
    doc = SchematicDoc.load(sch_path)
    test_uuid = "11111111-2222-3333-4444-555555555555"
    doc.update_managed_path(test_uuid)

    si = find_first(doc.root, "sheet_instances")
    assert si is not None
    path_node = find_first(si, "path")
    assert path_node is not None
    assert isinstance(path_node.items[1], StringNode)
    assert path_node.items[1].value == f"/{test_uuid}/"


def test_update_managed_path_qualifies_symbol_instances(tmp_path: Path) -> None:
    """update_managed_path replaces '/' with '/{uuid}/' in symbol instance paths."""
    sch_path = tmp_path / "managed.kicad_sch"
    sch_path.write_text(
        """(kicad_sch (version 20230121) (generator eeschema)
  (uuid "aaaa")
  (paper "A4")
  (lib_symbols)
  (symbol (lib_id "Device:R") (at 50.80 76.20 0) (unit 1)
    (uuid "sym-uuid-1")
    (instances
      (project "myproj" (path "/" (reference "R1") (unit 1)))
    )
  )
  (sheet_instances (path "/" (page "1")))
)
""",
        encoding="utf-8",
    )
    doc = SchematicDoc.load(sch_path)
    test_uuid = "aaaabbbb-cccc-dddd-eeee-ffffffffffff"
    doc.update_managed_path(test_uuid)

    # All (path ...) nodes in the document must be qualified — none may remain "/".
    for node in walk(doc.root):
        if (
            isinstance(node, ListNode)
            and node.key == "path"
            and len(node.items) >= 2
            and isinstance(node.items[1], StringNode)
        ):
            assert node.items[1].value != "/", (
                "Found unqualified '/' path after update_managed_path"
            )
            assert node.items[1].value == f"/{test_uuid}/", (
                f"Expected /{test_uuid}/, got {node.items[1].value!r}"
            )


def test_flat_layout_circuit_in_root_schematic(tmp_path: Path) -> None:
    """Flat layout: generated circuit lives directly in the root schematic file.

    There is no OpenClaw_Managed.kicad_sch sub-sheet.  The root schematic
    carries the ownership marker and contains all placed symbols.
    """
    ir_data = {
        "version": "1",
        "components": [{"ref": "R1", "symbol": "TestLib:R", "value": "10k"}],
        "nets": [{"name": "VCC", "pins": [{"ref": "R1", "pin": "1"}]}],
    }
    fixtures_dir = Path(__file__).resolve().parent.parent / "fixtures" / "symbols"
    ir_path = tmp_path / "ir.json"
    ir_path.write_text(json.dumps(ir_data), encoding="utf-8")

    result = cmd_new_from_netlist(
        Namespace(
            name="FlatTest",
            out_dir=str(tmp_path),
            description="",
            netlist=str(ir_path),
            symbols_dir=str(fixtures_dir),
            mode="internal",
        )
    )

    # Root schematic must exist and parse.
    root_doc = SchematicDoc.load(result.schematic_path)

    # No sub-sheet reference in the root schematic.
    assert root_doc.has_managed_sheet(sheet_name="OpenClaw_Managed") is False

    # No OpenClaw_Managed.kicad_sch file on disk.
    assert not (result.schematic_path.parent / MANAGED_SHEET_FILE).exists()

    # Component is in the root schematic.
    symbols = root_doc.list_symbols()
    refs = [s["ref"] for s in symbols]
    assert "R1" in refs

    # managed_schematic_path equals the root schematic in flat mode.
    assert result.managed_schematic_path == result.schematic_path
