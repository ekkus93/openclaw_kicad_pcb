"""Graphviz layout: diagnostics, serialization, and debug-dump helpers."""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from pathlib import Path

from .snap import LayoutHeuristicPolicy

_log = logging.getLogger(__name__)

_LAYOUT_DEBUG_ARTIFACTS: tuple[str, ...] = (
    "tiers",
    "connector_roles",
    "connector_role_summary",
    "diagnostics",
    "block_layout",
    "heuristic_profile_name",
    "layout_heuristic_policy",
    "placement_constraints",
    "halo_map",
    "halo_alignment",
    "decoupling_map",
    "sds_scores",
    "sds_columns",
    "dot_source",
    "raw_graphviz_positions",
    "post_snap_positions",
    "final_positions",
)


def _serialize_layout_positions(
    positions: Mapping[str, tuple[float, float, float | None]],
) -> dict[str, dict[str, float | None]]:
    """Return a JSON-friendly mapping for layout position triples."""
    return {
        ref: {
            "x": float(x),
            "y": float(y),
            "rotation": None if rotation is None else float(rotation),
        }
        for ref, (x, y, rotation) in sorted(positions.items())
    }


def _serialize_block_layout(block_layout: object) -> dict[str, object]:
    """Return a JSON-friendly view of block assignments and zones."""
    assignments = getattr(block_layout, "assignments", {})
    zones = getattr(block_layout, "zones", {})
    return {
        "assignments": {
            ref: {
                "role": assignment.role.value,
                "confidence": float(assignment.confidence),
                "reason": assignment.reason,
            }
            for ref, assignment in sorted(assignments.items())
        },
        "zones": {
            role.value: [float(value) for value in zone]
            for role, zone in sorted(zones.items(), key=lambda item: item[0].value)
        },
    }


def _layout_debug_artifact_manifest() -> dict[str, object]:
    """Describe the stable top-level artifacts emitted in layout debug dumps."""
    return {
        "version": 1,
        "artifacts": list(_LAYOUT_DEBUG_ARTIFACTS),
    }


def _serialize_layout_heuristic_policy(policy: LayoutHeuristicPolicy) -> dict[str, bool]:
    """Return the active post-layout heuristic toggles for debug dumps."""
    return {
        "enable_decoupling_snap": policy.enable_decoupling_snap,
        "enable_opamp_locality": policy.enable_opamp_locality,
        "enable_input_stage_cohesion": policy.enable_input_stage_cohesion,
        "enable_output_stage_cohesion": policy.enable_output_stage_cohesion,
    }


def _serialize_placement_constraints(
    *,
    decoupling_map: Mapping[str, str],
    feedback_refs: set[str],
    halo: Mapping[str, str],
    power_unit_refs: set[str],
    unit_sibling_pairs: list[tuple[str, str]],
) -> dict[str, object]:
    """Return the stable placement constraints that shaped the layout."""
    return {
        "decoupling_map": dict(sorted(decoupling_map.items())),
        "feedback_refs": sorted(feedback_refs),
        "halo_map": dict(sorted(halo.items())),
        "power_unit_refs": sorted(power_unit_refs),
        "unit_sibling_pairs": [list(pair) for pair in sorted(unit_sibling_pairs)],
    }


def _analyze_legacy_sds_fallback(roles: Mapping[str, str]) -> dict[str, object]:
    """Describe how older connector-role gating would have classified this role set."""
    input_refs = sorted(ref for ref, role in roles.items() if role == "input")
    output_refs = sorted(ref for ref, role in roles.items() if role == "output")
    power_refs = sorted(ref for ref, role in roles.items() if role == "power")
    unknown_refs = sorted(ref for ref, role in roles.items() if role == "unknown")

    missing_roles: list[str] = []
    if not input_refs:
        missing_roles.append("input")
    if not output_refs:
        missing_roles.append("output")

    return {
        "input_refs": input_refs,
        "output_refs": output_refs,
        "power_refs": power_refs,
        "unknown_refs": unknown_refs,
        "missing_roles": missing_roles,
        "would_trigger_legacy_bfs_fallback": bool(missing_roles),
        "legacy_mode": "bfs_fallback" if missing_roles else "sds_recursive_halving",
    }


def _build_layout_diagnostics(
    connector_role_summary: Mapping[str, object],
) -> list[dict[str, object]]:
    """Return diagnostics describing legacy-risk role classifications.

    These diagnostics do not indicate that the current Graphviz engine switched
    to a separate fallback placer. They record when older connector-role-gated
    logic would have degraded into lower-quality BFS-based column assignment.
    """
    diagnostics: list[dict[str, object]] = []
    raw_missing_roles = connector_role_summary.get("missing_roles", [])
    raw_unknown_refs = connector_role_summary.get("unknown_refs", [])
    missing_roles = (
        [str(role) for role in raw_missing_roles] if isinstance(raw_missing_roles, list) else []
    )
    unknown_refs = (
        [str(ref) for ref in raw_unknown_refs] if isinstance(raw_unknown_refs, list) else []
    )

    if missing_roles:
        diagnostics.append(
            {
                "code": "LAYDBG001",
                "severity": "warning",
                "message": (
                    "Connector-role inference is incomplete; the current Graphviz pipeline "
                    "stays active, but older connector-role gating would have degraded to "
                    "BFS fallback."
                ),
                "details": {
                    "missing_roles": missing_roles,
                    "unknown_refs": unknown_refs,
                    "legacy_mode": connector_role_summary.get("legacy_mode"),
                },
            }
        )
    elif unknown_refs:
        diagnostics.append(
            {
                "code": "LAYDBG002",
                "severity": "debug",
                "message": (
                    "Some connectors remain unknown, but input/output role detection is still "
                    "sufficient to keep SDS layout active."
                ),
                "details": {
                    "unknown_refs": unknown_refs,
                    "legacy_mode": connector_role_summary.get("legacy_mode"),
                },
            }
        )

    return diagnostics


def _emit_layout_diagnostics(diagnostics: list[dict[str, object]]) -> None:
    """Emit layout diagnostics through the module logger."""
    for diagnostic in diagnostics:
        code = diagnostic["code"]
        message = diagnostic["message"]
        details = diagnostic["details"]
        if diagnostic["severity"] == "warning":
            _log.warning("%s: %s details=%s", code, message, details)
        else:
            _log.debug("%s: %s details=%s", code, message, details)


def _analyze_halo_column_alignment(
    raw_positions: Mapping[str, tuple[float, float, float | None]],
    post_snap_positions: Mapping[str, tuple[float, float, float | None]],
    halo: Mapping[str, str],
    *,
    tolerance_mm: float = 0.5,
) -> dict[str, dict[str, object]]:
    """Describe raw vs post-snap column alignment for each halo member."""
    alignment: dict[str, dict[str, object]] = {}
    for halo_ref, anchor_ref in sorted(halo.items()):
        raw_member = raw_positions.get(halo_ref)
        raw_anchor = raw_positions.get(anchor_ref)
        post_member = post_snap_positions.get(halo_ref)
        post_anchor = post_snap_positions.get(anchor_ref)

        raw_dx = None
        raw_same_column = None
        if raw_member is not None and raw_anchor is not None:
            raw_dx = float(raw_member[0] - raw_anchor[0])
            raw_same_column = abs(raw_dx) <= tolerance_mm

        post_dx = None
        post_same_column = None
        if post_member is not None and post_anchor is not None:
            post_dx = float(post_member[0] - post_anchor[0])
            post_same_column = abs(post_dx) <= tolerance_mm

        alignment[halo_ref] = {
            "anchor_ref": anchor_ref,
            "raw_dx": raw_dx,
            "raw_same_column": raw_same_column,
            "post_snap_dx": post_dx,
            "post_snap_same_column": post_same_column,
            "moved_to_anchor_x_by_post_snap": (
                raw_same_column is False and post_same_column is True
            ),
        }
    return alignment


def _write_layout_debug_dump(path: Path, payload: dict[str, object]) -> None:
    """Write the requested layout debug payload to disk."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
