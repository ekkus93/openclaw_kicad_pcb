"""Graphviz layout: GraphvizLayoutEngine and its preparation helpers."""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

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
from ._gv_debug import (
    _analyze_halo_column_alignment,
    _analyze_legacy_sds_fallback,
    _build_layout_diagnostics,
    _emit_layout_diagnostics,
    _layout_debug_artifact_manifest,
    _serialize_block_layout,
    _serialize_layout_heuristic_policy,
    _serialize_layout_positions,
    _serialize_placement_constraints,
    _write_layout_debug_dump,
)
from ._gv_decoupling import _refine_shared_rail_decoupling_map
from .cache import _layout_cache_key, _load_layout_cache_entry, _save_layout_cache
from .dot_builder import _build_dot_source, _find_decoupling_caps, _safe_id
from .snap import (
    DEFAULT_LAYOUT_HEURISTIC_POLICY,
    ORIGIN_X,
    ORIGIN_Y,
    SCALE_MM_PER_GV,
    LayoutHeuristicPolicy,
    _apply_post_layout_snaps,
    _gv_to_kicad,
    _parse_plain_positions,
    _snap_input_connector_signal_attachment,
    snap_positions,
)

if TYPE_CHECKING:
    from ..block_detection import BlockLayout
    from ..circuit_ir import CircuitIR
    from ..layout import ComponentAnnotation

_log = logging.getLogger(__name__)

_MAX_ATTEMPTS = 2
_MAX_GRAPHVIZ_TIMEOUT_S = 60.0
_GRAPHVIZ_TIMEOUT_PER_ELEMENT_S = 0.12


def _graphviz_timeout_for_ir(ir: CircuitIR, *, base_timeout: float) -> float:
    """Return a Graphviz timeout budget scaled to graph size."""
    graph_elements = len(ir.components) + len(ir.nets)
    scaled_timeout = float(graph_elements) * _GRAPHVIZ_TIMEOUT_PER_ELEMENT_S
    return max(base_timeout, min(_MAX_GRAPHVIZ_TIMEOUT_S, scaled_timeout))


def _snap_final_symbol_positions(
    positions: dict[str, tuple[float, float, float | None]],
) -> dict[str, tuple[float, float, float | None]]:
    """Snap ordinary symbols while preserving explicit power-row anchors."""
    regular = {ref: pos for ref, pos in positions.items() if not ref.startswith("#")}
    snapped_regular = snap_positions(regular)
    return {
        ref: positions[ref] if ref.startswith("#") else snapped_regular[ref]
        for ref in positions
    }


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
        self._run_timeout_override: float | None = None

    def compute_symbol_positions(
        self, ir: CircuitIR
    ) -> dict[str, tuple[float, float, float | None]]:
        """Run ``dot`` on *ir* and return ``{ref: (x_mm, y_mm, rotation_deg)}``.

        **Rotation:** Orientation is computed via
        :func:`~kicad_pcb.layout.compute_orientations` after Graphviz layout
        and merged into each tuple.  Connectors at tier 0 get 0°; last-tier
        connectors get 180°; series passives get 0° (or 90° when y-spread
        dominates); shunt/bypass passives get 90°.

        **Snapping:** Ordinary component positions are snapped to the KiCad
        50-mil grid (1.27 mm) after all late layout passes. Explicit ``#PWR``
        and ``#FLG`` helpers retain their dedicated rail-row anchors.

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
            self._run_timeout_override = _graphviz_timeout_for_ir(ir, base_timeout=self._timeout)
            positions = self._run_dot(dot_source)
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                f"Graphviz 'dot' failed: {exc}.  Command: {self._dot} -Tplain -Gstart={self._seed}"
            ) from exc
        finally:
            self._run_timeout_override = None

        if not positions:
            raise RuntimeError(
                "Graphviz 'dot' returned no node positions.  "
                f"Command: {self._dot} -Tplain -Gstart={self._seed}"
            )

        missing = [r for r in refs if _safe_id(r) not in positions]
        if missing:
            raise RuntimeError(
                f"Graphviz 'dot' did not return positions for refs: {missing!r}.  "
                "Check the DOT graph for isolated nodes or unsupported syntax, "
                "or file a bug with the circuit IR."
            )

        safe_to_ref = {_safe_id(r): r for r in refs}
        raw_result: dict[str, tuple[float, float, float | None]] = {
            safe_to_ref[sid]: pos for sid, pos in positions.items() if sid in safe_to_ref
        }
        decoupling_map = _refine_shared_rail_decoupling_map(ir, raw_result, decoupling_map)

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
        result = _snap_input_connector_signal_attachment(
            result,
            ir,
            block_layout=block_layout,
        )
        result = _snap_final_symbol_positions(result)

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

        if self._cache_path is not None:
            _save_layout_cache(
                self._cache_path,
                cache_key,
                result,
                decoupling_map=decoupling_map,
            )
            _log.debug("Layout cache written (key %s…).", cache_key[:8])

        return result

    def _run_dot(self, dot_source: str) -> dict[str, tuple[float, float, float | None]]:
        """Invoke ``dot`` on *dot_source* and return parsed KiCad positions."""
        _log.debug("DOT source:\n%s", dot_source)

        cmd: list[str] = [self._dot, "-Tplain", f"-Gstart={self._seed}"]
        timeout = self._run_timeout_override or self._timeout
        for attempt in range(_MAX_ATTEMPTS):
            try:
                result = subprocess.run(  # noqa: S603
                    cmd,
                    input=dot_source,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    check=False,
                )
            except (FileNotFoundError, PermissionError) as exc:
                raise RuntimeError(f"dot binary not executable: {exc}") from exc
            except subprocess.TimeoutExpired as exc:
                if attempt < _MAX_ATTEMPTS - 1:
                    _log.debug("dot timed out (attempt %d), retrying", attempt + 1)
                    continue
                raise RuntimeError(f"dot timed out after {timeout}s") from exc

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
