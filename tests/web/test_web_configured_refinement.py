from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from kicad_pcb.errors import UserError
from kicad_pcb_web.services import configured_refinement as entrypoint
from kicad_pcb_web.services.refinement_config import RefinementFeatureConfig


def test_configured_refinement_disabled_never_dispatches(monkeypatch, tmp_path: Path) -> None:
    accepted = tmp_path / "accepted.kicad_sch"
    accepted.write_bytes(b"accepted")
    before = accepted.read_bytes()

    def unexpected_refine(**kwargs):
        raise AssertionError("disabled refinement must not dispatch")

    monkeypatch.setattr(entrypoint, "refine_schematic", unexpected_refine)
    with pytest.raises(UserError, match="disabled by configuration") as exc_info:
        entrypoint.run_configured_refinement(
            accepted_path=accepted,
            runtime=object(),  # type: ignore[arg-type]
            session_id="disabled-session",
            config=RefinementFeatureConfig(),
        )

    assert exc_info.value.code == "REFINEMENT_DISABLED"
    assert accepted.read_bytes() == before


def test_configured_refinement_enabled_uses_only_canonical_service(
    monkeypatch, tmp_path: Path
) -> None:
    accepted = tmp_path / "accepted.kicad_sch"
    accepted.write_bytes(b"accepted")
    observed: dict[str, object] = {}
    expected = SimpleNamespace(stop_reason="REFINEMENT_STOP_NO_OPERATIONS")

    def fake_refine(**kwargs):
        observed.update(kwargs)
        return expected

    monkeypatch.setattr(entrypoint, "refine_schematic", fake_refine)
    config = RefinementFeatureConfig(
        enabled=True,
        max_rounds=5,
        max_operations_per_round=3,
        max_total_accepted_operations=7,
        max_candidate_rejections=4,
        max_critic_repairs=1,
        max_planner_repairs=2,
    )
    runtime = object()

    result = entrypoint.run_configured_refinement(
        accepted_path=accepted,
        runtime=runtime,  # type: ignore[arg-type]
        session_id="enabled-session",
        config=config,
    )

    assert result is expected
    assert observed["accepted_path"] == accepted
    assert observed["runtime"] is runtime
    assert observed["session_id"] == "enabled-session"
    limits = observed["limits"]
    assert limits.max_rounds == 5  # type: ignore[attr-defined]
    assert limits.max_operations_per_round == 3  # type: ignore[attr-defined]
    assert limits.max_total_accepted_operations == 7  # type: ignore[attr-defined]
    assert limits.max_candidate_rejections == 4  # type: ignore[attr-defined]
    assert limits.max_critic_repairs == 1  # type: ignore[attr-defined]
    assert limits.max_planner_repairs == 2  # type: ignore[attr-defined]
