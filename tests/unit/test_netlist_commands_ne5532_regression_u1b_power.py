"""NE5532 regression tests — power profile debug dump and stage seam tests."""

from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path
from typing import cast

import pytest

from kicad_pcb.commands.netlist import (
    cmd_new_from_netlist,
)
from kicad_pcb.sch_doc import SchematicDoc
from kicad_pcb.sexpr.nodes import ListNode, StringNode
from kicad_pcb.sexpr.utils import walk
from tests import NE5532_HEADPHONE_REVIEW_FIXTURE, SYMBOLS_FIXTURE_DIR

pytestmark = pytest.mark.unit

_KICAD_SYSTEM_SYMBOLS = Path("/usr/share/kicad/symbols")
_REAL_NE5532_SYMBOLS = SYMBOLS_FIXTURE_DIR
_REAL_NE5532_REVIEW_NETLIST = NE5532_HEADPHONE_REVIEW_FIXTURE.netlist_path

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
