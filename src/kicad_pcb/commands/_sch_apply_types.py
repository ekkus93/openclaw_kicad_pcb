"""Schematic apply: shared types, constants, and heuristic profiles."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TypedDict

from ..component_types import TIER_SPACING_MM
from ..graphviz_layout import DEFAULT_LAYOUT_HEURISTIC_POLICY, LayoutHeuristicPolicy
from ..router import DEFAULT_ROUTING_HEURISTIC_POLICY, RoutingHeuristicPolicy

MANAGED_SHEET_NAME = "OpenClaw_Managed"
MANAGED_SHEET_FILE = "OpenClaw_Managed.kicad_sch"
# MANAGED_SHEET_NAME / MANAGED_SHEET_FILE are kept for backward-compatibility
# with any existing projects that still have the sub-sheet on disk.
_LOCAL_DECOUPLING_DISTANCE_WARN_MM = TIER_SPACING_MM * 1.5


def _prefer_decoupling_side_candidates(
    *,
    cap_y: float,
    rail_polarity: str | None,
    candidate_refs: list[str],
    raw_layout: dict[str, tuple[float, float, float | None]],
) -> list[str]:
    if rail_polarity == "positive":
        same_side = [ref for ref in candidate_refs if raw_layout[ref][1] > cap_y]
        return same_side or candidate_refs
    if rail_polarity == "negative":
        same_side = [ref for ref in candidate_refs if raw_layout[ref][1] < cap_y]
        return same_side or candidate_refs
    return candidate_refs


@dataclass(frozen=True)
class SchematicHeuristicProfile:
    """Bundle layout and routing heuristic policies under one named profile."""

    name: str
    layout_policy: LayoutHeuristicPolicy = DEFAULT_LAYOUT_HEURISTIC_POLICY
    routing_policy: RoutingHeuristicPolicy = DEFAULT_ROUTING_HEURISTIC_POLICY


ANALOG_AUDIO_HEURISTIC_PROFILE = SchematicHeuristicProfile(
    name="analog_audio",
    routing_policy=RoutingHeuristicPolicy(
        enable_compact_output_tails=True,
        enable_compact_local_ground_clusters=True,
        enable_small_analog_local_routing=True,
    ),
)
GENERIC_DIGITAL_HEURISTIC_PROFILE = SchematicHeuristicProfile(
    name="generic_digital",
    layout_policy=LayoutHeuristicPolicy(
        enable_decoupling_snap=False,
        enable_opamp_locality=False,
        enable_input_stage_cohesion=False,
        enable_output_stage_cohesion=False,
    ),
    routing_policy=RoutingHeuristicPolicy(
        enable_compact_output_tails=False,
        enable_compact_local_ground_clusters=False,
        enable_small_analog_local_routing=False,
    ),
)
POWER_SUPPLY_HEURISTIC_PROFILE = SchematicHeuristicProfile(
    name="power_supply",
    layout_policy=LayoutHeuristicPolicy(
        enable_decoupling_snap=False,
        enable_opamp_locality=False,
        enable_input_stage_cohesion=False,
        enable_output_stage_cohesion=False,
    ),
    routing_policy=RoutingHeuristicPolicy(
        enable_compact_output_tails=False,
        enable_compact_local_ground_clusters=True,
        enable_small_analog_local_routing=False,
    ),
)
DENSE_DEBUG_HEURISTIC_PROFILE = SchematicHeuristicProfile(
    name="dense_debug",
    layout_policy=LayoutHeuristicPolicy(
        enable_decoupling_snap=False,
        enable_opamp_locality=False,
        enable_input_stage_cohesion=False,
        enable_output_stage_cohesion=False,
    ),
    routing_policy=RoutingHeuristicPolicy(
        enable_compact_output_tails=False,
        enable_compact_local_ground_clusters=False,
        enable_small_analog_local_routing=False,
    ),
)
SCHEMATIC_HEURISTIC_PROFILES: dict[str, SchematicHeuristicProfile] = {
    profile.name: profile
    for profile in (
        ANALOG_AUDIO_HEURISTIC_PROFILE,
        GENERIC_DIGITAL_HEURISTIC_PROFILE,
        POWER_SUPPLY_HEURISTIC_PROFILE,
        DENSE_DEBUG_HEURISTIC_PROFILE,
    )
}
DEFAULT_SCHEMATIC_HEURISTIC_PROFILE = ANALOG_AUDIO_HEURISTIC_PROFILE


@dataclass(frozen=True)
class _ApplyNetlistRequest:
    netlist_path: Path
    symbols_dir: Path | None
    mode_name: str | None
    force: bool
    dry_run: bool
    backup: bool = False
    strict: bool = False
    layout_name: str | None = None
    routing_name: str | None = None
    label_mode_name: str | None = None
    heuristic_profile_name: str | None = None
    debug_dump_path: Path | None = None
    heuristic_profile: SchematicHeuristicProfile = DEFAULT_SCHEMATIC_HEURISTIC_PROFILE


@dataclass(frozen=True)
class _PlacedSymbolSpec:
    unit: int
    pin_nums: tuple[str, ...]
    logical_ref: str


class _UnitSplitDebugEntry(TypedDict):
    placed_ref: str
    pin_nums: list[str]
    symbol: str
    unit: int
