from __future__ import annotations

import pytest

from kicad_pcb.errors import UserError
from kicad_pcb_web.services.refinement_config import (
    RefinementFeatureConfig,
    load_refinement_feature_config,
    require_refinement_enabled,
)


def test_refinement_feature_is_default_off_with_conservative_bounds() -> None:
    config = load_refinement_feature_config({})

    assert config == RefinementFeatureConfig()
    assert not config.enabled
    assert config.max_rounds == 3
    assert config.max_operations_per_round == 4
    assert config.max_total_accepted_operations == 8
    assert config.max_candidate_rejections == 2
    assert config.max_critic_repairs == 0
    assert config.max_planner_repairs == 0


def test_refinement_feature_config_parses_explicit_values() -> None:
    config = load_refinement_feature_config(
        {
            "KICAD_WEBAPP_REFINEMENT_ENABLED": "true",
            "KICAD_WEBAPP_REFINEMENT_MAX_ROUNDS": "5",
            "KICAD_WEBAPP_REFINEMENT_MAX_OPERATIONS_PER_ROUND": "3",
            "KICAD_WEBAPP_REFINEMENT_MAX_TOTAL_ACCEPTED_OPERATIONS": "7",
            "KICAD_WEBAPP_REFINEMENT_MAX_CANDIDATE_REJECTIONS": "4",
            "KICAD_WEBAPP_REFINEMENT_MAX_CRITIC_REPAIRS": "1",
            "KICAD_WEBAPP_REFINEMENT_MAX_PLANNER_REPAIRS": "2",
        }
    )

    assert config.enabled
    limits = config.to_loop_limits()
    assert limits.max_rounds == 5
    assert limits.max_operations_per_round == 3
    assert limits.max_total_accepted_operations == 7
    assert limits.max_candidate_rejections == 4
    assert limits.max_critic_repairs == 1
    assert limits.max_planner_repairs == 2


@pytest.mark.parametrize("value", ["yes", "on", "TRUE ", "2", ""])
def test_refinement_feature_config_rejects_noncanonical_boolean(value: str) -> None:
    with pytest.raises(ValueError):
        load_refinement_feature_config({"KICAD_WEBAPP_REFINEMENT_ENABLED": value})


@pytest.mark.parametrize("value", [" 3", "3 ", "+3", "03", "3.0", "abc", ""])
def test_refinement_feature_config_rejects_noncanonical_integer(value: str) -> None:
    with pytest.raises(ValueError):
        load_refinement_feature_config({"KICAD_WEBAPP_REFINEMENT_MAX_ROUNDS": value})


def test_refinement_feature_config_reuses_loop_bound_validation() -> None:
    with pytest.raises(ValueError, match="max_rounds"):
        load_refinement_feature_config({"KICAD_WEBAPP_REFINEMENT_MAX_ROUNDS": "0"})
    with pytest.raises(ValueError, match="max_planner_repairs"):
        load_refinement_feature_config({"KICAD_WEBAPP_REFINEMENT_MAX_PLANNER_REPAIRS": "9"})


def test_refinement_disabled_gate_is_explicit() -> None:
    with pytest.raises(UserError, match="disabled by configuration") as exc_info:
        require_refinement_enabled(RefinementFeatureConfig())

    assert exc_info.value.code == "REFINEMENT_DISABLED"


def test_refinement_enabled_gate_allows_execution() -> None:
    require_refinement_enabled(RefinementFeatureConfig(enabled=True))
