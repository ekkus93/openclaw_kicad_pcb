"""Feature-gated entrypoint for bounded iterative schematic refinement."""

from __future__ import annotations

from pathlib import Path

from .refinement_config import RefinementFeatureConfig, require_refinement_enabled
from .schematic_refinement import RefinementLoopResult, RefinementRuntime, refine_schematic


def run_configured_refinement(
    *,
    accepted_path: Path,
    runtime: RefinementRuntime,
    session_id: str,
    config: RefinementFeatureConfig,
) -> RefinementLoopResult:
    """Dispatch only to the canonical transactional refine service when explicitly enabled."""

    require_refinement_enabled(config)
    return refine_schematic(
        accepted_path=accepted_path,
        runtime=runtime,
        session_id=session_id,
        limits=config.to_loop_limits(),
    )
