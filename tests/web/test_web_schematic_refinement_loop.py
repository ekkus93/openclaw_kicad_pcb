from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from kicad_pcb.errors import UserError
from kicad_pcb.refinement.operations import LayoutOperationBatchResult, LayoutOperationResult
from kicad_pcb_web.services import schematic_refinement as service


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


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
    )


def _rejected(current: str) -> service.RefinementApplyResult:
    return service.RefinementApplyResult(
        "rejected",
        "REFINEMENT_NO_DETERMINISTIC_IMPROVEMENT",
        current,
        current,
        "c" * 64,
        None,
        None,
        None,
        None,
        None,
    )


def _no_op(current: str) -> service.RefinementApplyResult:
    return service.RefinementApplyResult(
        "no_op",
        "REFINEMENT_NO_OPERATIONS",
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


def test_refine_rejection_limit_preserves_last_accepted(monkeypatch, tmp_path: Path) -> None:
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
