"""Graphviz-based schematic layout engine for kicad_pcb.

Uses ``dot -Tplain`` to lay out a *bipartite* graph of circuit components
and nets so that signals flow left → right (``rankdir=LR``).

Graph model (4.3 — bipartite)
------------------------------
* **Component nodes** — one node per reference designator (e.g. ``R1``).
* **Net nodes** — one node per net (id ``net_<name>``).
* **Edges** — connect each component to every net it participates in.
* **Power nets** (GND, VCC, VDD, V+, V- and similar short all-caps names)
  are *excluded* from the bipartite graph to avoid creating highly-connected
  hubs.  Components connected only via power nets are placed in a dedicated
  right-hand cluster.

Coordinate mapping
------------------
``dot -Tplain`` reports positions in Graphviz "point" units (72 pt/inch),
with the origin at the lower-left and y increasing upward.  We map to KiCad
mm coordinates with y increasing downward:

    x_mm = ORIGIN_X + gv_x × SCALE_MM_PER_GV
    y_mm = ORIGIN_Y + (max_gv_y − gv_y) × SCALE_MM_PER_GV

Public API
----------
:func:`find_dot_binary`     — locate the ``dot`` executable.
:class:`GraphvizLayoutEngine` — :class:`~kicad_pcb.layout_engine.LayoutEngine`
                                implementation.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..block_detection import BlockLayout
    from ..circuit_ir import CircuitIR
    from ..layout import ComponentAnnotation

from ..component_types import (
    component_type,
    is_power_net,
    normalize_gnd_net_name,
    power_rail_polarity,
)
from ..errors import ErrorCode, UserError
from ..layout import _compute_opamp_halo as _compute_opamp_halo_layout
from ..layout import compute_affinity_groups as _compute_affinity_groups
from ..layout import compute_orientations as _compute_orientations
from ..layout import compute_sds_columns as _compute_sds_columns
from ..layout import compute_signal_distance_scores as _compute_signal_distance_scores
from ..layout import detect_stereo_channels as _detect_stereo_channels
from ..layout import find_feedback_paths as _find_feedback_paths
from ..tier import assign_ic_units_to_tiers as _assign_ic_units_to_tiers
from ..tier import assign_tiers as _assign_tiers
from ..tier import build_ic_unit_sibling_constraints as _build_ic_unit_sibling_constraints
from ..tier import classify_connector_roles as _classify_connector_roles
from .cache import (
    _layout_cache_key,
    _LayoutCacheEntry,
    _load_layout_cache,
    _load_layout_cache_entry,
    _save_layout_cache,
)
from .dot_builder import (
    _assign_bfs_tiers,
    _build_dot_source,
    _compute_net_weights,
    _emit_decoupling_constraints,
    _find_decoupling_caps,
    _is_capacitor,
    _is_connector,
    _safe_id,
)
from .snap import (
    DEFAULT_LAYOUT_HEURISTIC_POLICY,
    GRID_ROW_MM,
    ORIGIN_X,
    ORIGIN_Y,
    PAGE_MAX_X,
    PAGE_MAX_Y,
    SCALE_MM_PER_GV,
    LayoutHeuristicPolicy,
    _apply_post_layout_snaps,
    _apply_stereo_split,
    _compact_y_gap,
    _deoverlap_positions,
    _fit_to_page,
    _gv_to_kicad,
    _parse_plain_positions,
    _post_snap_decoupling_caps,
    _post_stereo_barycentric,
    _snap_feedback_components,
    _snap_power_symbols,
    snap_positions,
)

_log = logging.getLogger(__name__)

# Maximum number of subprocess attempts (retry on transient failures).
_MAX_ATTEMPTS = 2

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


def _prefer_decoupling_side_candidates_from_layout(
    *,
    cap_y: float,
    rail_polarity: str | None,
    candidate_refs: list[str],
    raw_layout: Mapping[str, tuple[float, float, float | None]],
) -> list[str]:
    """Prefer the active-device side that matches the rail polarity."""
    if rail_polarity == "positive":
        same_side = [ref for ref in candidate_refs if raw_layout[ref][1] > cap_y]
        return same_side or candidate_refs
    if rail_polarity == "negative":
        same_side = [ref for ref in candidate_refs if raw_layout[ref][1] < cap_y]
        return same_side or candidate_refs
    return candidate_refs


def _refine_shared_rail_decoupling_map(
    ir: CircuitIR,
    raw_layout: Mapping[str, tuple[float, float, float | None]],
    decoupling_map: Mapping[str, str],
) -> dict[str, str]:
    """Refine ambiguous shared-rail decoupling anchors using raw layout geometry."""
    if not decoupling_map or not raw_layout:
        return dict(decoupling_map)

    component_nets: dict[str, set[str]] = {}
    for net in ir.nets:
        for pin_ref in net.pins:
            component_nets.setdefault(pin_ref.ref, set()).add(net.name)

    active_ics_by_rail: dict[str, list[str]] = {}
    for component in ir.components:
        if component_type(component.ref) != "ic" or component.ref not in raw_layout:
            continue

        nets = component_nets.get(component.ref, set())
        if not nets or not any(not is_power_net(net_name) for net_name in nets):
            continue

        for net_name in nets:
            if power_rail_polarity(net_name) is not None:
                active_ics_by_rail.setdefault(net_name, []).append(component.ref)

    refined_map = dict(decoupling_map)
    for cap_ref in decoupling_map:
        if cap_ref not in raw_layout:
            continue

        net_names = sorted(component_nets.get(cap_ref, set()))
        rail_nets = [name for name in net_names if power_rail_polarity(name) is not None]
        ground_nets = [name for name in net_names if normalize_gnd_net_name(name) == "GND"]
        if len(rail_nets) != 1 or len(ground_nets) != 1:
            continue

        candidate_refs = active_ics_by_rail.get(rail_nets[0], [])
        if len(candidate_refs) <= 1:
            continue

        cap_x, cap_y, _ = raw_layout[cap_ref]
        filtered_candidates = _prefer_decoupling_side_candidates_from_layout(
            cap_y=cap_y,
            rail_polarity=power_rail_polarity(rail_nets[0]),
            candidate_refs=candidate_refs,
            raw_layout=raw_layout,
        )
        refined_map[cap_ref] = min(
            filtered_candidates,
            key=lambda ref: (abs(raw_layout[ref][0] - cap_x), abs(raw_layout[ref][1] - cap_y), ref),
        )

    return refined_map


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


def _prepare_layout_inputs(
    ir: CircuitIR,
    refs: list[str],
    *,
    tiers: dict[str, int],
) -> tuple[
    BlockLayout,
    dict[str, str],
    dict[str, ComponentAnnotation],
    set[str],
    dict[str, str],
    dict[str, int],
    list[dict[str, object]],
    dict[str, object],
    set[str],
    list[tuple[str, str]],
]:
    """Collect derived layout inputs used by the Graphviz engine."""

    from ..block_detection import classify_circuit, debug_dump  # noqa: PLC0415

    block_layout = classify_circuit(ir)
    _log.debug("Block detection complete:\n%s", debug_dump(block_layout))

    decoupling_map = _find_decoupling_caps(ir)
    roles = _classify_connector_roles(refs, tiers, ir=ir)
    annotations = _find_feedback_paths(ir, tiers, roles=roles or None)
    feedback_refs: set[str] = {
        ref for ref, annotation in annotations.items() if annotation.feedback
    }
    halo = _compute_opamp_halo_layout(ir, annotations, tiers)
    sds_scores = _compute_signal_distance_scores(ir, roles)
    sds_cols = _compute_sds_columns(refs, sds_scores)
    legacy_sds_fallback = _analyze_legacy_sds_fallback(roles)
    diagnostics = _build_layout_diagnostics(legacy_sds_fallback)
    _emit_layout_diagnostics(diagnostics)

    unit_groups = _assign_ic_units_to_tiers(ir, tiers)
    power_unit_refs: set[str] = {
        group.power_unit for group in unit_groups.values() if group.power_unit is not None
    }
    unit_sibling_pairs = _build_ic_unit_sibling_constraints(unit_groups)

    return (
        block_layout,
        decoupling_map,
        annotations,
        feedback_refs,
        halo,
        sds_cols,
        diagnostics,
        legacy_sds_fallback,
        power_unit_refs,
        unit_sibling_pairs,
    )


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


# ---------------------------------------------------------------------------
# Binary discovery
# ---------------------------------------------------------------------------

# Current releases resolve Graphviz from ``GRAPHVIZ_DOT`` or the system PATH.
# The package-local ``bin/dot`` slot remains as a dormant compatibility hook so
# future distributions can ship a colocated binary without changing discovery
# code. In today's tree the path does not exist, so user-visible resolution is
# still env-var first, then PATH.
_BUNDLED_DOT_PATH: Path = Path(__file__).parent / "bin" / "dot"


def find_dot_binary(*, strict: bool = False) -> str | None:
    """Locate the ``dot`` binary used for Graphviz layout.

    Search order:

    1. **Package-local compatibility slot** — ``<package>/bin/dot`` if present.
    2. :envvar:`GRAPHVIZ_DOT` environment variable.
    3. System :data:`PATH` (``shutil.which``).

    Returns the resolved path string or ``None`` if not found.

    Use :func:`find_dot_source` to get both the path and discovery source.
    """
    # 1. Package-local compatibility slot.
    if _BUNDLED_DOT_PATH.is_file() and os.access(_BUNDLED_DOT_PATH, os.X_OK):
        return str(_BUNDLED_DOT_PATH)
    # 2. GRAPHVIZ_DOT environment variable.
    env_val = os.environ.get("GRAPHVIZ_DOT", "").strip()
    if env_val:
        if Path(env_val).is_file() and os.access(env_val, os.X_OK):
            return env_val
        if strict:
            raise UserError(
                "GRAPHVIZ_DOT must point to an executable file",
                code=ErrorCode.TOOL_ERROR,
                details={"GRAPHVIZ_DOT": env_val},
            )
    # 3. System PATH.
    return shutil.which("dot")


def find_dot_source(*, strict: bool = False) -> tuple[str, str] | None:
    """Return ``(path, source)`` for the resolved ``dot`` binary.

    *source* is one of ``"bundled"``, ``"GRAPHVIZ_DOT"``, or ``"PATH"``.
    Returns ``None`` if ``dot`` cannot be found.
    """
    if _BUNDLED_DOT_PATH.is_file() and os.access(_BUNDLED_DOT_PATH, os.X_OK):
        return str(_BUNDLED_DOT_PATH), "bundled"
    env_val = os.environ.get("GRAPHVIZ_DOT", "").strip()
    if env_val:
        if Path(env_val).is_file() and os.access(env_val, os.X_OK):
            return env_val, "GRAPHVIZ_DOT"
        if strict:
            raise UserError(
                "GRAPHVIZ_DOT must point to an executable file",
                code=ErrorCode.TOOL_ERROR,
                details={"GRAPHVIZ_DOT": env_val},
            )
    path = shutil.which("dot")
    if path:
        return path, "PATH"
    return None


# ---------------------------------------------------------------------------
# GraphvizLayoutEngine
# ---------------------------------------------------------------------------


class GraphvizLayoutEngine:
    """Calls ``dot -Tplain`` to produce left-to-right schematic layouts.

    Parameters
    ----------
    dot_path:
        Absolute (or PATH-resolved) path to the ``dot`` executable.
    scale:
        Scale factor (mm per Graphviz point unit). Default: ``SCALE_MM_PER_GV``.
    timeout:
        Subprocess timeout in seconds. Default: 10.
    """

    def __init__(  # noqa: PLR0913
        self,
        *,
        dot_path: str,
        scale: float = SCALE_MM_PER_GV,
        timeout: float = 10.0,
        seed: int = 7,
        cache_path: Path | None = None,
        debug_dump_path: Path | None = None,
        heuristic_profile_name: str | None = None,
        tiers: dict[str, int] | None = None,
        layout_heuristic_policy: LayoutHeuristicPolicy = DEFAULT_LAYOUT_HEURISTIC_POLICY,
        strict: bool = False,
    ) -> None:
        self._dot = dot_path
        self._scale = scale
        self._timeout = timeout
        self._seed = seed
        self._cache_path = cache_path
        self._debug_dump_path = debug_dump_path
        self._heuristic_profile_name = heuristic_profile_name
        self._tiers = tiers
        self._layout_heuristic_policy = layout_heuristic_policy
        self._strict = strict

    # ----------------------------------------------------------------
    # LayoutEngine Protocol
    # ----------------------------------------------------------------

    def compute_symbol_positions(
        self, ir: CircuitIR
    ) -> dict[str, tuple[float, float, float | None]]:
        """Run ``dot`` on *ir* and return ``{ref: (x_mm, y_mm, rotation_deg)}``.

        **Rotation:** Orientation is computed via
        :func:`~kicad_pcb.layout.compute_orientations` after Graphviz layout
        and merged into each tuple.  Connectors at tier 0 get 0°; last-tier
        connectors get 180°; series passives get 0° (or 90° when y-spread
        dominates); shunt/bypass passives get 90°.

        **Snapping:** All positions are snapped to the KiCad 50-mil grid
        (1.27 mm) after the Graphviz raw output is processed.

        **Cache:** If *cache_path* was supplied and a cached result exists for
        the current circuit topology (keyed by sha256 of the DOT source), the
        cached positions are returned immediately without invoking ``dot``.
        After a fresh run the result is written back to the cache.

        **Determinism:** ``dot`` is invoked with ``-Gstart=<seed>`` (default
        7) so that repeated runs on identical input produce stable output.

        Raises :class:`RuntimeError` if ``dot`` fails or returns no positions.
        """
        refs = sorted(c.ref for c in ir.components)
        if not refs:
            return {}

        _tiers = self._tiers if self._tiers is not None else _assign_tiers(ir, strict=self._strict)
        (
            block_layout,
            decoupling_map,
            annotations,
            feedback_refs,
            halo,
            sds_cols,
            diagnostics,
            legacy_sds_fallback,
            _power_unit_refs,
            _unit_sibling_pairs,
        ) = _prepare_layout_inputs(ir, refs, tiers=_tiers)
        _roles = _classify_connector_roles(refs, _tiers, ir=ir)
        sds_scores = _compute_signal_distance_scores(ir, _roles)

        # Build DOT source up-front so we can derive the cache key.
        # Pass pre-computed tiers so _build_dot_source skips a redundant assign_tiers call.
        # Compute affinity order so _emit_tier_subgraphs emits nodes in signal-flow
        # coupling order rather than alphabetical order, giving dot a better start.
        affinity_order = _compute_affinity_groups(ir, _tiers)
        dot_source = _build_dot_source(
            ir,
            decoupling_map=decoupling_map,
            feedback_refs=feedback_refs or None,
            power_unit_refs=_power_unit_refs or None,
            unit_sibling_pairs=_unit_sibling_pairs or None,
            tiers=_tiers,
            connector_roles=_roles or None,
            halo=halo or None,
            sds_cols=sds_cols or None,
            affinity_order=affinity_order,
            block_layout=block_layout,
        )
        cache_key = _layout_cache_key(dot_source)

        # --- Cache hit: return immediately without invoking dot. ---
        if self._cache_path is not None:
            cached_entry = _load_layout_cache_entry(self._cache_path, cache_key)
            if cached_entry is not None:
                cached = cached_entry.positions
                _log.debug("Layout cache hit (key %s…); skipping dot.", cache_key[:8])
                decoupling_map = dict(cached_entry.decoupling_map)
                if self._debug_dump_path is not None:
                    _write_layout_debug_dump(
                        self._debug_dump_path,
                        {
                            "artifact_manifest": _layout_debug_artifact_manifest(),
                            "block_layout": _serialize_block_layout(block_layout),
                            "cache_hit": True,
                            "cache_key": cache_key,
                            "connector_role_summary": legacy_sds_fallback,
                            "diagnostics": diagnostics,
                            "connector_roles": dict(sorted(_roles.items())),
                            "decoupling_map": dict(sorted(decoupling_map.items())),
                            "dot_path": self._dot,
                            "dot_source": dot_source,
                            "feedback_refs": sorted(feedback_refs),
                            "final_positions": _serialize_layout_positions(cached),
                            "heuristic_profile_name": self._heuristic_profile_name,
                            "halo_alignment": _analyze_halo_column_alignment({}, cached, halo),
                            "halo_map": dict(sorted(halo.items())),
                            "layout_heuristic_policy": _serialize_layout_heuristic_policy(
                                self._layout_heuristic_policy
                            ),
                            "placement_constraints": _serialize_placement_constraints(
                                decoupling_map=decoupling_map,
                                feedback_refs=feedback_refs,
                                halo=halo,
                                power_unit_refs=_power_unit_refs,
                                unit_sibling_pairs=_unit_sibling_pairs,
                            ),
                            "post_snap_positions": _serialize_layout_positions(cached),
                            "raw_graphviz_positions": {},
                            "sds_columns": dict(sorted(sds_cols.items())),
                            "sds_scores": {
                                ref: float(score) for ref, score in sorted(sds_scores.items())
                            },
                            "seed": self._seed,
                            "tiers": dict(sorted(_tiers.items())),
                        },
                    )
                return cached

        try:
            positions = self._run_dot(dot_source)
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                f"Graphviz 'dot' failed: {exc}.  Command: {self._dot} -Tplain -Gstart={self._seed}"
            ) from exc

        if not positions:
            raise RuntimeError(
                "Graphviz 'dot' returned no node positions.  "
                f"Command: {self._dot} -Tplain -Gstart={self._seed}"
            )

        # Verify all refs are covered.
        missing = [r for r in refs if _safe_id(r) not in positions]
        if missing:
            raise RuntimeError(
                f"Graphviz 'dot' did not return positions for refs: {missing!r}.  "
                "Check the DOT graph for isolated nodes or unsupported syntax, "
                "or file a bug with the circuit IR."
            )

        # Re-key from safe_id → original ref
        safe_to_ref = {_safe_id(r): r for r in refs}
        raw_result: dict[str, tuple[float, float, float | None]] = {
            safe_to_ref[sid]: pos for sid, pos in positions.items() if sid in safe_to_ref
        }
        decoupling_map = _refine_shared_rail_decoupling_map(ir, raw_result, decoupling_map)

        # Post-layout: apply all snap passes in canonical order (grid → power
        # → feedback → stereo split → decoupling caps).  See
        # gv_snap._apply_post_layout_snaps for the ordering rationale.
        channels = _detect_stereo_channels(ir)
        post_snap_result = _apply_post_layout_snaps(
            raw_result,
            ir,
            feedback_refs=feedback_refs,
            annotations=annotations,
            channels=channels,
            decoupling_map=decoupling_map,
            roles=_roles or None,
            halo=halo or None,
            power_unit_refs=frozenset(_power_unit_refs),
            unit_sibling_pairs=tuple(_unit_sibling_pairs),
            block_layout=block_layout,
            heuristic_policy=self._layout_heuristic_policy,
            strict=self._strict,
        )

        # Compute component orientations (rotation in degrees) from signal topology
        # and merge into the result so callers receive (x, y, rotation) triples.
        _plain_positions: dict[str, tuple[float, float]] = {
            ref: (x, y) for ref, (x, y, _) in post_snap_result.items()
        }
        _orientations = _compute_orientations(
            ir, _plain_positions, _tiers, roles=_roles or None, block_layout=block_layout
        )
        result: dict[str, tuple[float, float, float | None]] = {
            ref: (x, y, float(_orientations.get(ref, 0)))
            for ref, (x, y, _) in post_snap_result.items()
        }

        if self._debug_dump_path is not None:
            halo_alignment = _analyze_halo_column_alignment(raw_result, post_snap_result, halo)
            _write_layout_debug_dump(
                self._debug_dump_path,
                {
                    "artifact_manifest": _layout_debug_artifact_manifest(),
                    "block_layout": _serialize_block_layout(block_layout),
                    "cache_hit": False,
                    "cache_key": cache_key,
                    "connector_role_summary": legacy_sds_fallback,
                    "diagnostics": diagnostics,
                    "connector_roles": dict(sorted(_roles.items())),
                    "decoupling_map": dict(sorted(decoupling_map.items())),
                    "dot_path": self._dot,
                    "dot_source": dot_source,
                    "feedback_refs": sorted(feedback_refs),
                    "final_positions": _serialize_layout_positions(result),
                    "heuristic_profile_name": self._heuristic_profile_name,
                    "forced_same_column_halo_refs": sorted(
                        halo_ref
                        for halo_ref, details in halo_alignment.items()
                        if details["post_snap_same_column"] is True
                    ),
                    "halo_alignment": halo_alignment,
                    "halo_map": dict(sorted(halo.items())),
                    "layout_heuristic_policy": _serialize_layout_heuristic_policy(
                        self._layout_heuristic_policy
                    ),
                    "placement_constraints": _serialize_placement_constraints(
                        decoupling_map=decoupling_map,
                        feedback_refs=feedback_refs,
                        halo=halo,
                        power_unit_refs=_power_unit_refs,
                        unit_sibling_pairs=_unit_sibling_pairs,
                    ),
                    "post_snap_positions": _serialize_layout_positions(post_snap_result),
                    "raw_graphviz_positions": _serialize_layout_positions(raw_result),
                    "sds_columns": dict(sorted(sds_cols.items())),
                    "sds_scores": {ref: float(score) for ref, score in sorted(sds_scores.items())},
                    "seed": self._seed,
                    "tiers": dict(sorted(_tiers.items())),
                },
            )

        # --- Cache write: persist for next run. ---
        if self._cache_path is not None:
            _save_layout_cache(
                self._cache_path,
                cache_key,
                result,
                decoupling_map=decoupling_map,
            )
            _log.debug("Layout cache written (key %s…).", cache_key[:8])

        return result

    # ----------------------------------------------------------------
    # Private helpers
    # ----------------------------------------------------------------

    def _run_dot(self, dot_source: str) -> dict[str, tuple[float, float, float | None]]:
        """Invoke ``dot`` on *dot_source* and return parsed KiCad positions.

        Passes ``-Gstart=<seed>`` to make layout deterministic across repeated
        runs on the same input.
        """
        _log.debug("DOT source:\n%s", dot_source)

        cmd: list[str] = [self._dot, "-Tplain", f"-Gstart={self._seed}"]
        for attempt in range(_MAX_ATTEMPTS):
            try:
                result = subprocess.run(  # noqa: S603
                    cmd,
                    input=dot_source,
                    capture_output=True,
                    text=True,
                    timeout=self._timeout,
                    check=False,
                )
            except (FileNotFoundError, PermissionError) as exc:
                raise RuntimeError(f"dot binary not executable: {exc}") from exc
            except subprocess.TimeoutExpired as exc:
                if attempt < _MAX_ATTEMPTS - 1:
                    _log.debug("dot timed out (attempt %d), retrying", attempt + 1)
                    continue
                raise RuntimeError(f"dot timed out after {self._timeout}s") from exc

            gv_positions = _parse_plain_positions(result.stdout)
            if result.returncode != 0:
                stderr_lines = [line for line in result.stderr.splitlines() if line.strip()]
                if (
                    gv_positions
                    and stderr_lines
                    and all(line.startswith("Warning:") for line in stderr_lines)
                ):
                    _log.debug(
                        "dot returned code %d with warning-only stderr; "
                        "proceeding with parsed positions",
                        result.returncode,
                    )
                    break
                raise RuntimeError(
                    f"dot exited with code {result.returncode}:\n{result.stderr[:400]}"
                )
            break

        return _gv_to_kicad(
            gv_positions,
            origin_x=ORIGIN_X,
            origin_y=ORIGIN_Y,
            scale=self._scale,
        )

    @property
    def dot_path(self) -> str:
        return self._dot

    def __repr__(self) -> str:
        return f"GraphvizLayoutEngine(dot_path={self._dot!r})"


# ---------------------------------------------------------------------------
# Helpers exported for tests
# ---------------------------------------------------------------------------

__all__ = [
    "apply_post_layout_snaps",
    "apply_stereo_split",
    "assign_bfs_tiers",
    "build_dot_source",
    "compact_y_gap",
    "compute_net_weights",
    "deoverlap_positions",
    "emit_decoupling_constraints",
    "find_decoupling_caps",
    "find_dot_binary",
    "find_dot_source",
    "fit_to_page",
    "GraphvizLayoutEngine",
    "GRID_ROW_MM",
    "LayoutHeuristicPolicy",
    "is_capacitor",
    "is_connector",
    "layout_cache_key",
    "load_layout_cache",
    "ORIGIN_X",
    "ORIGIN_Y",
    "PAGE_MAX_X",
    "PAGE_MAX_Y",
    "parse_plain_positions",
    "post_snap_decoupling_caps",
    "save_layout_cache",
    "snap_feedback_components",
    "snap_positions",
    "snap_power_symbols",
]

# ---------------------------------------------------------------------------
# Backwards-compatible re-exports (public names for tests and external code)
# The functions below are defined in sub-modules (gv_dot_builder, gv_snap,
# gv_cache) with a leading underscore.  The aliases here expose them as
# public names for backwards compatibility and direct test access.
# New code should import from graphviz_layout (not from the sub-modules).
# ---------------------------------------------------------------------------
apply_post_layout_snaps = _apply_post_layout_snaps
apply_stereo_split = _apply_stereo_split
assign_bfs_tiers = _assign_bfs_tiers
build_dot_source = _build_dot_source
compact_y_gap = _compact_y_gap
compute_net_weights = _compute_net_weights
deoverlap_positions = _deoverlap_positions
emit_decoupling_constraints = _emit_decoupling_constraints
find_decoupling_caps = _find_decoupling_caps
refine_shared_rail_decoupling_map = _refine_shared_rail_decoupling_map
fit_to_page = _fit_to_page
is_capacitor = _is_capacitor
is_connector = _is_connector
layout_cache_key = _layout_cache_key
load_layout_cache = _load_layout_cache
load_layout_cache_entry = _load_layout_cache_entry
LayoutCacheEntry = _LayoutCacheEntry
parse_plain_positions = _parse_plain_positions
post_snap_decoupling_caps = _post_snap_decoupling_caps
post_stereo_barycentric = _post_stereo_barycentric
save_layout_cache = _save_layout_cache
snap_feedback_components = _snap_feedback_components
snap_power_symbols = _snap_power_symbols
