"""Warning IR helpers and Phase 1 warning suite tests."""

from __future__ import annotations

import json
import math
from argparse import Namespace
from collections import defaultdict
from pathlib import Path
from typing import cast

import pytest

from kicad_pcb.commands.netlist import (
    cmd_validate_netlist,
)
from kicad_pcb.lint.helpers import _collect_wire_segments
from kicad_pcb.sch_doc import SchematicDoc
from kicad_pcb.sexpr.nodes import AtomNode, ListNode, StringNode
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


def _write_input_bypass_warning_ir(path: Path) -> None:
    payload = {
        "version": "1",
        "components": [
            {"ref": "J1", "symbol": "TestLib:R", "value": "Input"},
            {"ref": "C5", "symbol": "TestLib:R", "value": "1u"},
            {"ref": "R1", "symbol": "TestLib:R", "value": "100k"},
            {"ref": "RV1", "symbol": "TestLib:R", "value": "10k"},
        ],
        "nets": [
            {
                "name": "LEFT_IN",
                "pins": [
                    {"ref": "J1", "pin": "1"},
                    {"ref": "C5", "pin": "1"},
                    {"ref": "R1", "pin": "1"},
                ],
            },
            {
                "name": "IN_L_AC",
                "pins": [
                    {"ref": "C5", "pin": "2"},
                    {"ref": "R1", "pin": "2"},
                    {"ref": "RV1", "pin": "1"},
                ],
            },
            {
                "name": "GND",
                "pins": [
                    {"ref": "J1", "pin": "2"},
                    {"ref": "RV1", "pin": "2"},
                ],
            },
        ],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_output_bypass_warning_ir(path: Path) -> None:
    payload = {
        "version": "1",
        "components": [
            {"ref": "U1", "symbol": "TestLib:R", "value": "Driver"},
            {"ref": "C7", "symbol": "TestLib:R", "value": "220u"},
            {"ref": "R8", "symbol": "TestLib:R", "value": "47"},
            {"ref": "J2", "symbol": "TestLib:R", "value": "Output"},
        ],
        "nets": [
            {
                "name": "OUT_L_STAGE2_RAW",
                "pins": [
                    {"ref": "U1", "pin": "1"},
                    {"ref": "C7", "pin": "1"},
                    {"ref": "R8", "pin": "1"},
                ],
            },
            {
                "name": "HP_L_OUT",
                "pins": [
                    {"ref": "C7", "pin": "2"},
                    {"ref": "R8", "pin": "2"},
                    {"ref": "J2", "pin": "1"},
                ],
            },
        ],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_output_load_warning_ir(path: Path) -> None:
    payload = {
        "version": "1",
        "components": [
            {"ref": "U1", "symbol": "TestLib:SingleOpAmp", "value": "Driver"},
            {"ref": "C1", "symbol": "Device:C", "value": "220u"},
            {"ref": "J2", "symbol": "TestLib:Conn3", "value": "Output"},
        ],
        "nets": [
            {
                "name": "U1_OUT",
                "pins": [
                    {"ref": "U1", "pin": "3"},
                    {"ref": "C1", "pin": "1"},
                ],
            },
            {
                "name": "HP_L_OUT",
                "pins": [
                    {"ref": "C1", "pin": "2"},
                    {"ref": "J2", "pin": "1"},
                ],
            },
            {
                "name": "VIN",
                "pins": [{"ref": "U1", "pin": "1"}],
            },
            {
                "name": "U1_INV",
                "pins": [{"ref": "U1", "pin": "2"}],
            },
        ],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_connector_ambiguity_ir(path: Path) -> None:
    payload = {
        "version": "1",
        "components": [
            {"ref": "J1", "symbol": "TestLib:Conn3", "value": "Stereo-ish"},
            {"ref": "R1", "symbol": "TestLib:R", "value": "10k"},
        ],
        "nets": [
            {
                "name": "IN",
                "pins": [
                    {"ref": "J1", "pin": "1"},
                    {"ref": "R1", "pin": "1"},
                ],
            },
            {
                "name": "GND",
                "pins": [
                    {"ref": "J1", "pin": "2"},
                    {"ref": "R1", "pin": "2"},
                ],
            },
        ],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_feedback_warning_ir(path: Path) -> None:
    payload = {
        "version": "1",
        "components": [
            {"ref": "U1", "symbol": "TestLib:SingleOpAmp", "value": "Gain"},
            {"ref": "R1", "symbol": "TestLib:R", "value": "10k"},
            {"ref": "J1", "symbol": "TestLib:R", "value": "Out"},
        ],
        "nets": [
            {
                "name": "VIN",
                "pins": [
                    {"ref": "U1", "pin": "1"},
                    {"ref": "R1", "pin": "1"},
                ],
            },
            {
                "name": "U1_INV",
                "pins": [
                    {"ref": "U1", "pin": "2"},
                    {"ref": "R1", "pin": "2"},
                ],
            },
            {
                "name": "U1_OUT",
                "pins": [
                    {"ref": "U1", "pin": "3"},
                    {"ref": "J1", "pin": "1"},
                ],
            },
        ],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_stage_topology_warning_ir(path: Path) -> None:
    payload = {
        "version": "1",
        "components": [
            {"ref": "U1", "symbol": "TestLib:SingleOpAmp", "value": "Gain"},
            {"ref": "R2", "symbol": "Device:R", "value": "10k"},
            {"ref": "J1", "symbol": "TestLib:Conn3", "value": "In"},
        ],
        "nets": [
            {
                "name": "VIN",
                "pins": [
                    {"ref": "U1", "pin": "1"},
                    {"ref": "J1", "pin": "1"},
                ],
            },
            {
                "name": "U1_INV",
                "pins": [
                    {"ref": "U1", "pin": "2"},
                    {"ref": "R2", "pin": "1"},
                ],
            },
            {
                "name": "U1_OUT",
                "pins": [
                    {"ref": "U1", "pin": "3"},
                    {"ref": "R2", "pin": "2"},
                ],
            },
        ],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_output_floating_warning_ir(path: Path) -> None:
    payload = {
        "version": "1",
        "components": [
            {"ref": "U1", "symbol": "TestLib:SingleOpAmp", "value": "Gain"},
            {"ref": "R1", "symbol": "TestLib:R", "value": "10k"},
        ],
        "nets": [
            {
                "name": "VIN",
                "pins": [
                    {"ref": "U1", "pin": "1"},
                    {"ref": "R1", "pin": "1"},
                ],
            },
            {
                "name": "U1_INV",
                "pins": [
                    {"ref": "U1", "pin": "2"},
                    {"ref": "R1", "pin": "2"},
                ],
            },
        ],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_output_short_warning_ir(path: Path) -> None:
    payload = {
        "version": "1",
        "components": [
            {"ref": "U1", "symbol": "TestLib:SingleOpAmp", "value": "Gain"},
            {"ref": "R1", "symbol": "TestLib:R", "value": "10k"},
            {"ref": "P1", "symbol": "TestLib:R", "value": "Rail"},
        ],
        "nets": [
            {
                "name": "VIN",
                "pins": [
                    {"ref": "U1", "pin": "1"},
                    {"ref": "R1", "pin": "1"},
                ],
            },
            {
                "name": "U1_INV",
                "pins": [
                    {"ref": "U1", "pin": "2"},
                    {"ref": "R1", "pin": "2"},
                ],
            },
            {
                "name": "VPLUS15",
                "pins": [
                    {"ref": "U1", "pin": "3"},
                    {"ref": "P1", "pin": "1"},
                ],
            },
            {
                "name": "BIAS",
                "pins": [
                    {"ref": "P1", "pin": "2"},
                ],
            },
        ],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_speaker_driver_warning_ir(path: Path) -> None:
    payload = {
        "version": "1",
        "components": [
            {
                "ref": "U1",
                "symbol": "Amplifier_Operational:NE5532",
                "value": "NE5532",
            },
            {
                "ref": "J1",
                "symbol": "Connector:AudioJack3",
                "value": "Speaker Out",
            },
        ],
        "nets": [
            {"name": "VIN", "pins": [{"ref": "U1", "pin": "3"}]},
            {"name": "U1_INV", "pins": [{"ref": "U1", "pin": "2"}]},
            {
                "name": "SPEAKER_OUT",
                "pins": [
                    {"ref": "U1", "pin": "1"},
                    {"ref": "J1", "pin": "T"},
                ],
            },
            {"name": "VMINUS15", "pins": [{"ref": "U1", "pin": "4"}]},
            {"name": "VPLUS15", "pins": [{"ref": "U1", "pin": "8"}]},
            {"name": "GND", "pins": [{"ref": "J1", "pin": "S"}]},
        ],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def _normalize_warning_entries(
    warnings: tuple[dict[str, object], ...],
) -> list[tuple[str, tuple[tuple[str, object], ...]]]:
    normalized: list[tuple[str, tuple[tuple[str, object], ...]]] = []
    for warning in warnings:
        code = warning.get("code")
        if not isinstance(code, str):
            continue
        details_obj = warning.get("details")
        details = cast(dict[str, object], details_obj) if isinstance(details_obj, dict) else {}
        normalized.append((code, tuple(sorted(details.items()))))
    return sorted(normalized)


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


class TestPhase1WarningSuite:
    @pytest.mark.parametrize(
        ("filename", "writer", "expected_codes"),
        [
            (
                "warning_ir.json",
                _write_input_bypass_warning_ir,
                {"INPUT_COUPLING_BYPASSED_BY_RESISTOR"},
            ),
            (
                "output_warning_ir.json",
                _write_output_bypass_warning_ir,
                {"OUTPUT_COUPLING_BYPASSED_BY_RESISTOR"},
            ),
            (
                "connector_warning_ir.json",
                _write_connector_ambiguity_ir,
                {"CONNECTOR_UNUSED_PINS_AMBIGUOUS"},
            ),
            (
                "feedback_warning_ir.json",
                _write_feedback_warning_ir,
                {"OPAMP_FEEDBACK_MISSING_OR_NONLOCAL"},
            ),
            (
                "stage_topology_warning_ir.json",
                _write_stage_topology_warning_ir,
                {"OPAMP_STAGE_TOPOLOGY_LIKELY_MISTAKEN"},
            ),
            (
                "output_load_warning_ir.json",
                _write_output_load_warning_ir,
                {"OUTPUT_CAP_NO_DEFINED_LOAD_OR_BLEED"},
            ),
            (
                "output_floating_warning_ir.json",
                _write_output_floating_warning_ir,
                {"OPAMP_OUTPUT_FLOATING"},
            ),
            (
                "output_short_warning_ir.json",
                _write_output_short_warning_ir,
                {"OPAMP_OUTPUT_SHORTED_TO_RAIL"},
            ),
            (
                "speaker_driver_warning_ir.json",
                _write_speaker_driver_warning_ir,
                {"OPAMP_PRESENTED_AS_SPEAKER_POWER_STAGE"},
            ),
        ],
        ids=[
            "input-coupling",
            "output-coupling",
            "connector-ambiguity",
            "missing-feedback",
            "stage-topology-likely-mistaken",
            "output-cap-no-load-or-bleed",
            "output-floating",
            "output-shorted-to-rail",
            "speaker-power-stage",
        ],
    )
    def test_synthetic_warning_fixtures_cover_each_phase1_family(
        self,
        tmp_path: Path,
        filename: str,
        writer,
        expected_codes: set[str],
    ) -> None:
        ir_path = tmp_path / filename
        writer(ir_path)
        fixtures_dir = Path(__file__).resolve().parent.parent / "fixtures" / "symbols"

        result = cmd_validate_netlist(
            Namespace(
                netlist=str(ir_path),
                symbols_dir=str(fixtures_dir),
            )
        )

        codes = {warning["code"] for warning in result.warnings}
        assert expected_codes <= codes
