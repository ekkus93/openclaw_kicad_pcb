"""Strict environment-backed configuration for iterative schematic refinement."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import asdict, dataclass

from kicad_pcb.errors import UserError

from .schematic_refinement import RefinementLoopLimits

_REFINEMENT_ENV_PREFIX = "KICAD_WEBAPP_REFINEMENT_"
_REFINEMENT_ENV_KEYS = frozenset(
    {
        f"{_REFINEMENT_ENV_PREFIX}ENABLED",
        f"{_REFINEMENT_ENV_PREFIX}MAX_ROUNDS",
        f"{_REFINEMENT_ENV_PREFIX}MAX_OPERATIONS_PER_ROUND",
        f"{_REFINEMENT_ENV_PREFIX}MAX_TOTAL_ACCEPTED_OPERATIONS",
        f"{_REFINEMENT_ENV_PREFIX}MAX_CANDIDATE_REJECTIONS",
        f"{_REFINEMENT_ENV_PREFIX}MAX_CRITIC_REPAIRS",
        f"{_REFINEMENT_ENV_PREFIX}MAX_PLANNER_REPAIRS",
    }
)


@dataclass(frozen=True)
class RefinementFeatureConfig:
    """Validated feature gate and resource bounds for iterative refinement."""

    enabled: bool = False
    max_rounds: int = 3
    max_operations_per_round: int = 4
    max_total_accepted_operations: int = 8
    max_candidate_rejections: int = 2
    max_critic_repairs: int = 0
    max_planner_repairs: int = 0

    def __post_init__(self) -> None:
        if type(self.enabled) is not bool:
            raise ValueError("enabled must be a boolean")
        self.to_loop_limits()

    def to_loop_limits(self) -> RefinementLoopLimits:
        return RefinementLoopLimits(
            max_rounds=self.max_rounds,
            max_operations_per_round=self.max_operations_per_round,
            max_total_accepted_operations=self.max_total_accepted_operations,
            max_candidate_rejections=self.max_candidate_rejections,
            max_critic_repairs=self.max_critic_repairs,
            max_planner_repairs=self.max_planner_repairs,
        )

    def to_safe_dict(self) -> dict[str, object]:
        return asdict(self)


def load_refinement_feature_config(
    environ: Mapping[str, str] | None = None,
) -> RefinementFeatureConfig:
    """Load the refinement feature gate from environment variables with no coercive fallback."""

    source = os.environ if environ is None else environ
    _reject_unknown_refinement_settings(source)
    defaults = RefinementFeatureConfig()
    return RefinementFeatureConfig(
        enabled=_parse_bool(
            source,
            "ENABLED",
            default=defaults.enabled,
        ),
        max_rounds=_parse_int(
            source,
            "MAX_ROUNDS",
            default=defaults.max_rounds,
        ),
        max_operations_per_round=_parse_int(
            source,
            "MAX_OPERATIONS_PER_ROUND",
            default=defaults.max_operations_per_round,
        ),
        max_total_accepted_operations=_parse_int(
            source,
            "MAX_TOTAL_ACCEPTED_OPERATIONS",
            default=defaults.max_total_accepted_operations,
        ),
        max_candidate_rejections=_parse_int(
            source,
            "MAX_CANDIDATE_REJECTIONS",
            default=defaults.max_candidate_rejections,
        ),
        max_critic_repairs=_parse_int(
            source,
            "MAX_CRITIC_REPAIRS",
            default=defaults.max_critic_repairs,
        ),
        max_planner_repairs=_parse_int(
            source,
            "MAX_PLANNER_REPAIRS",
            default=defaults.max_planner_repairs,
        ),
    )


def require_refinement_enabled(config: RefinementFeatureConfig) -> None:
    if not config.enabled:
        raise UserError(
            "Iterative schematic refinement is disabled by configuration.",
            code="REFINEMENT_DISABLED",
        )


def _reject_unknown_refinement_settings(source: Mapping[str, str]) -> None:
    unknown = sorted(
        key
        for key in source
        if key.startswith(_REFINEMENT_ENV_PREFIX) and key not in _REFINEMENT_ENV_KEYS
    )
    if unknown:
        raise ValueError(f"Unsupported refinement setting(s): {', '.join(unknown)}")


def _parse_bool(
    source: Mapping[str, str],
    suffix: str,
    *,
    default: bool,
) -> bool:
    key = _REFINEMENT_ENV_PREFIX + suffix
    raw = source.get(key)
    if raw is None:
        return default
    if raw == "1" or raw.lower() == "true":
        return True
    if raw == "0" or raw.lower() == "false":
        return False
    raise ValueError(f"{key} must be one of: 0, 1, false, true")


def _parse_int(
    source: Mapping[str, str],
    suffix: str,
    *,
    default: int,
) -> int:
    key = _REFINEMENT_ENV_PREFIX + suffix
    raw = source.get(key)
    if raw is None:
        return default
    if not raw or raw.strip() != raw or raw.startswith("+"):
        raise ValueError(f"{key} must be a canonical base-10 integer")
    try:
        value = int(raw, 10)
    except ValueError as exc:
        raise ValueError(f"{key} must be a canonical base-10 integer") from exc
    if str(value) != raw:
        raise ValueError(f"{key} must be a canonical base-10 integer")
    return value
