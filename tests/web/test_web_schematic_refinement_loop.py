from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from kicad_pcb.errors import UserError
from kicad_pcb.refinement.operations import LayoutOperationBatchResult, LayoutOperationResult
from kicad_pcb_web.services import schematic_refinement as service


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture(autouse=True)
def _synthetic_layout_fingerprint(monkeypatch) -> None:
    monkeypatch.setattr(
        service,
        "compute_schematic_layout_fingerprint",
        lambda path: SimpleNamespace(digest=_sha(path)),
    )


def _runtime(tmp_path: Path) -> service.RefinementRuntime:
    return service.RefinementRuntime(
        authoritative_ir=object(),  # type: ignore[arg-type]
        adapter=object(),  # type: ignore[arg-type]
        llm_client=object(),  # type: ignore[arg-type]
        work_dir=tmp_path / "work",
        evidence_root=tmp_path / "evidence",
    )


def _accepted(before: str, after: str, *, count: int = 1) -> service.RefinementApplyResult:
    operations = LayoutOperationBatchResult(
        before,
        after,
        tuple(
            LayoutOperationResult(f"op-{index}", "move_component", "applied", {})
            for index in range(count)
        ),
    )
    return service.RefinementApplyResult(
        "accepted",
        "REFINEMENT_ACCEPTED",
        before,
        after,
        after,
        None,
        operations,
        None,
        None,
        None,
        candidate_layout_fingerprint=after,
    )


def _rejected(
    current: str,
    *,
    code: str = "REFINEMENT_PROTECTED_METRIC_REGRESSION",
    candidate_hash: str = "c" * 64,
    candidate_layout_fingerprint: str | None = "c" * 64,
) -> service.RefinementApplyResult:
    return service.RefinementApplyResult(
        "rejected",
        code,
        current,
        current,
        candidate_hash,
        None,
        None,
        None,
        None,
        None,
        candidate_layout_fingerprint=candidate_layout_fingerprint,
    )


def _no_op(
    current: str,
    *,
    code: str = "REFINEMENT_NO_OPERATIONS",
) -> service.RefinementApplyResult:
    return service.RefinementApplyResult(
        "no_op",
        code,
        current,
        current,
        None,
        None,
        None,
        None,
        None,
        None,
    )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_rounds": 0},
        {"max_rounds": True},
        {"max_operations_per_round": 33},
        {"max_total_accepted_operations": -1},
        {"max_candidate_rejections": 0},
        {"max_critic_repairs": 9},
        {"max_planner_repairs": -1},
    ],
)
def test_loop_limits_reject_invalid_values(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        service.RefinementLoopLimits(**kwargs)  # type: ignore[arg-type]


def test_refine_no_op_stops_after_one_round(monkeypatch, tmp_path: Path) -> None:
    accepted = tmp_path / "accepted.kicad_sch"
    accepted.write_bytes(b"A")
    calls = 0

    def apply_once(**kwargs):
        nonlocal calls
        calls += 1
        assert kwargs["enforce_best_known_retention"] is True
        assert isinstance(kwargs["runtime"].llm_client, service.RefinementModelCallBudget)
        assert kwargs["runtime"].llm_client.max_calls == 6
        assert kwargs["runtime"].prior_decisions == ()
        return _no_op(_sha(kwargs["accepted_path"]))

    monkeypatch.setattr(service, "apply_once_schematic_refinement", apply_once)
    result = service.refine_schematic(
        accepted_path=accepted,
        runtime=_runtime(tmp_path),
        session_id="s1",
    )
    assert calls == 1
    assert result.stop_reason == "REFINEMENT_STOP_NO_OPERATIONS"
    assert result.rounds_attempted == 1
    assert result.final_accepted_hash == _sha(accepted)
    assert result.starting_layout_fingerprint == _sha(accepted)
    assert result.final_layout_fingerprint == _sha(accepted)
    assert result.model_calls_made == 0
    assert result.model_call_limit == 6


def test_refine_no_actionable_issues_has_distinct_stop_reason(monkeypatch, tmp_path: Path) -> None:
    accepted = tmp_path / "accepted.kicad_sch"
    accepted.write_bytes(b"A")

    monkeypatch.setattr(
        service,
        "apply_once_schematic_refinement",
        lambda **kwargs: _no_op(
            _sha(kwargs["accepted_path"]),
            code="REFINEMENT_NO_ACTIONABLE_CRITIC_ISSUES",
        ),
    )
    result = service.refine_schematic(
        accepted_path=accepted,
        runtime=_runtime(tmp_path),
        session_id="no-issues",
    )

    assert result.stop_reason == "REFINEMENT_STOP_NO_ACTIONABLE_ISSUES"
    assert result.rounds_attempted == 1
    assert result.accepted_rounds == 0
    assert accepted.read_bytes() == b"A"


def test_refine_rejection_limit_preserves_last_accepted(monkeypatch, tmp_path: Path) -> None:
    accepted = tmp_path / "accepted.kicad_sch"
    accepted.write_bytes(b"A")
    call = 0
    observed_history: list[tuple[service.RefinementDecisionHistoryEntry, ...]] = []

    def apply_once(**kwargs):
        nonlocal call
        call += 1
        observed_history.append(kwargs["runtime"].prior_decisions)
        path = kwargs["accepted_path"]
        before = _sha(path)
        if call == 1:
            path.write_bytes(b"B")
            return _accepted(before, _sha(path))
        return _rejected(before)

    monkeypatch.setattr(service, "apply_once_schematic_refinement", apply_once)
    result = service.refine_schematic(
        accepted_path=accepted,
        runtime=_runtime(tmp_path),
        session_id="s2",
        limits=service.RefinementLoopLimits(max_rounds=5, max_candidate_rejections=1),
    )
    assert result.stop_reason == "REFINEMENT_STOP_REJECTION_LIMIT"
    assert result.accepted_rounds == 1
    assert result.rejected_rounds == 1
    assert accepted.read_bytes() == b"B"
    assert result.final_accepted_hash == _sha(accepted)
    assert result.best_accepted_hash == result.final_accepted_hash
    assert result.latest_attempted_hash == "c" * 64
    assert observed_history[0] == ()
    assert len(observed_history[1]) == 1
    assert observed_history[1][0].status == "accepted"
    assert observed_history[1][0].code == "REFINEMENT_ACCEPTED"
    assert observed_history[1][0].operation_types == ("move_component",)
    assert observed_history[1][0].candidate_layout_fingerprint == _sha(accepted)


def test_refine_no_meaningful_improvement_stops_immediately(monkeypatch, tmp_path: Path) -> None:
    accepted = tmp_path / "accepted.kicad_sch"
    accepted.write_bytes(b"A")

    def apply_once(**kwargs):
        current = _sha(kwargs["accepted_path"])
        return _rejected(
            current,
            code="REFINEMENT_NO_DETERMINISTIC_IMPROVEMENT",
            candidate_hash="d" * 64,
            candidate_layout_fingerprint="d" * 64,
        )

    monkeypatch.setattr(service, "apply_once_schematic_refinement", apply_once)
    result = service.refine_schematic(
        accepted_path=accepted,
        runtime=_runtime(tmp_path),
        session_id="no-improvement",
        limits=service.RefinementLoopLimits(max_rounds=5, max_candidate_rejections=4),
    )

    assert result.stop_reason == "REFINEMENT_STOP_NO_MEANINGFUL_IMPROVEMENT"
    assert result.rounds_attempted == 1
    assert result.rejected_rounds == 1
    assert accepted.read_bytes() == b"A"


def test_refine_enforces_total_operation_budget(monkeypatch, tmp_path: Path) -> None:
    accepted = tmp_path / "accepted.kicad_sch"
    accepted.write_bytes(b"A")
    observed_max_operations: list[int] = []

    def apply_once(**kwargs):
        path = kwargs["accepted_path"]
        round_limits = kwargs["limits"]
        observed_max_operations.append(round_limits.max_operations)
        before = _sha(path)
        path.write_bytes(path.read_bytes() + b"x")
        return _accepted(before, _sha(path), count=round_limits.max_operations)

    monkeypatch.setattr(service, "apply_once_schematic_refinement", apply_once)
    result = service.refine_schematic(
        accepted_path=accepted,
        runtime=_runtime(tmp_path),
        session_id="s3",
        limits=service.RefinementLoopLimits(
            max_rounds=5,
            max_operations_per_round=2,
            max_total_accepted_operations=3,
        ),
    )
    assert observed_max_operations == [2, 1]
    assert result.accepted_operations == 3
    assert result.stop_reason == "REFINEMENT_STOP_OPERATION_BUDGET"


def test_refine_three_round_improvement_stops_at_max_rounds(monkeypatch, tmp_path: Path) -> None:
    accepted = tmp_path / "accepted.kicad_sch"
    accepted.write_bytes(b"A")
    history_lengths: list[int] = []

    def apply_once(**kwargs):
        path = kwargs["accepted_path"]
        history_lengths.append(len(kwargs["runtime"].prior_decisions))
        before = _sha(path)
        path.write_bytes(path.read_bytes() + b"x")
        return _accepted(before, _sha(path))

    monkeypatch.setattr(service, "apply_once_schematic_refinement", apply_once)
    result = service.refine_schematic(
        accepted_path=accepted,
        runtime=_runtime(tmp_path),
        session_id="three-rounds",
        limits=service.RefinementLoopLimits(max_rounds=3),
    )

    assert history_lengths == [0, 1, 2]
    assert result.stop_reason == "REFINEMENT_STOP_MAX_ROUNDS"
    assert result.rounds_attempted == 3
    assert result.accepted_rounds == 3
    assert result.accepted_operations == 3
    assert accepted.read_bytes() == b"Axxx"
    assert result.final_accepted_hash == _sha(accepted)
    assert result.best_accepted_hash == result.final_accepted_hash


def test_refine_detects_exact_state_oscillation(monkeypatch, tmp_path: Path) -> None:
    accepted = tmp_path / "accepted.kicad_sch"
    accepted.write_bytes(b"A")
    starting_hash = _sha(accepted)
    call = 0

    def apply_once(**kwargs):
        nonlocal call
        call += 1
        path = kwargs["accepted_path"]
        before = _sha(path)
        path.write_bytes(b"B" if call == 1 else b"A")
        return _accepted(before, _sha(path))

    monkeypatch.setattr(service, "apply_once_schematic_refinement", apply_once)
    result = service.refine_schematic(
        accepted_path=accepted,
        runtime=_runtime(tmp_path),
        session_id="s4",
        limits=service.RefinementLoopLimits(max_rounds=5),
    )
    assert result.stop_reason == "REFINEMENT_STOP_OSCILLATION"
    assert result.rounds_attempted == 2
    assert result.final_accepted_hash == starting_hash


def test_refine_detects_rejected_inverse_layout_cycle(monkeypatch, tmp_path: Path) -> None:
    accepted = tmp_path / "accepted.kicad_sch"
    accepted.write_bytes(b"A")
    starting_layout = _sha(accepted)

    def apply_once(**kwargs):
        current = _sha(kwargs["accepted_path"])
        return _rejected(
            current,
            candidate_hash="d" * 64,
            candidate_layout_fingerprint=starting_layout,
        )

    monkeypatch.setattr(service, "apply_once_schematic_refinement", apply_once)
    result = service.refine_schematic(
        accepted_path=accepted,
        runtime=_runtime(tmp_path),
        session_id="inverse-cycle",
        limits=service.RefinementLoopLimits(max_rounds=5, max_candidate_rejections=4),
    )

    assert result.stop_reason == "REFINEMENT_STOP_OSCILLATION"
    assert result.rounds_attempted == 1
    assert result.rejected_rounds == 1
    assert result.final_accepted_hash == _sha(accepted)


def test_refine_hard_failure_leaves_last_accepted_round_intact(monkeypatch, tmp_path: Path) -> None:
    accepted = tmp_path / "accepted.kicad_sch"
    accepted.write_bytes(b"A")
    call = 0

    def apply_once(**kwargs):
        nonlocal call
        call += 1
        path = kwargs["accepted_path"]
        before = _sha(path)
        if call == 1:
            path.write_bytes(b"B")
            return _accepted(before, _sha(path))
        raise UserError("provider failed", code="LLM_PROVIDER_FAILED")

    monkeypatch.setattr(service, "apply_once_schematic_refinement", apply_once)
    with pytest.raises(UserError, match="provider failed"):
        service.refine_schematic(
            accepted_path=accepted,
            runtime=_runtime(tmp_path),
            session_id="s5",
            limits=service.RefinementLoopLimits(max_rounds=5),
        )
    assert accepted.read_bytes() == b"B"


def test_refine_rejects_unsafe_session_id(tmp_path: Path) -> None:
    accepted = tmp_path / "accepted.kicad_sch"
    accepted.write_bytes(b"A")
    with pytest.raises(UserError, match="Invalid schematic refinement session id"):
        service.refine_schematic(
            accepted_path=accepted,
            runtime=_runtime(tmp_path),
            session_id="../escape",
        )
