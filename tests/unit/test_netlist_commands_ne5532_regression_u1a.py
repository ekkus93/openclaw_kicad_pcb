"""NE5532 fixture regression tests: route, position, and label quality."""

from __future__ import annotations

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
from kicad_pcb.sexpr.utils import find_first
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
