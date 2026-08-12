from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from kicad_pcb.errors import UserError
from kicad_pcb.refinement.session_reservation import reserve_refinement_session_namespace
from kicad_pcb_web.services import schematic_refinement as service


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture(autouse=True)
def _synthetic_refinement_fingerprints(monkeypatch) -> None:
    monkeypatch.setattr(
        service,
        "compute_schematic_layout_fingerprint",
        lambda path: SimpleNamespace(digest=_sha(path)),
    )
    monkeypatch.setattr(
        service,
        "build_circuit_ir_fingerprint",
        lambda ir: SimpleNamespace(sha256=lambda: "a" * 64),
    )


def _runtime(tmp_path: Path) -> service.RefinementRuntime:
    return service.RefinementRuntime(
        authoritative_ir=object(),  # type: ignore[arg-type]
        adapter=object(),  # type: ignore[arg-type]
        llm_client=object(),  # type: ignore[arg-type]
        work_dir=tmp_path / "work",
        evidence_root=tmp_path / "evidence",
        provenance=service.RefinementProvenance(
            provider="fake-provider",
            model="fake-model",
            product_version="0.1.0",
            implementation_sha="f" * 40,
        ),
    )


def _no_op(current: str) -> service.RefinementApplyResult:
    return service.RefinementApplyResult(
        status="no_op",
        code="REFINEMENT_NO_OPERATIONS",
        accepted_hash_before=current,
        accepted_hash_after=current,
        candidate_hash=None,
        evidence_dir=None,
        operations=None,
        electrical=None,
        structural=None,
        quality=None,
    )


def test_completed_session_cannot_be_replayed_before_model_or_mutation(
    monkeypatch, tmp_path: Path
) -> None:
    accepted = tmp_path / "accepted.kicad_sch"
    accepted.write_bytes(b"A")
    runtime = _runtime(tmp_path)
    calls = 0

    def apply_once(**kwargs):
        nonlocal calls
        calls += 1
        return _no_op(_sha(kwargs["accepted_path"]))

    monkeypatch.setattr(service, "apply_once_schematic_refinement", apply_once)
    first = service.refine_schematic(
        accepted_path=accepted,
        runtime=runtime,
        session_id="completed-session",
    )

    reservation = runtime.evidence_root / "sessions" / ".reservations" / "completed-session.lock"
    assert first.session_evidence_dir is not None
    assert first.session_evidence_dir.is_dir()
    assert not reservation.exists()
    assert calls == 1

    with pytest.raises(UserError, match="session evidence already exists") as exc_info:
        service.refine_schematic(
            accepted_path=accepted,
            runtime=runtime,
            session_id="completed-session",
        )

    assert exc_info.value.code == "REFINEMENT_EVIDENCE_EXISTS"
    assert calls == 1
    assert accepted.read_bytes() == b"A"


def test_active_reservation_blocks_execution_before_model_or_mutation(
    monkeypatch, tmp_path: Path
) -> None:
    accepted = tmp_path / "accepted.kicad_sch"
    accepted.write_bytes(b"A")
    runtime = _runtime(tmp_path)
    existing = reserve_refinement_session_namespace(
        runtime.evidence_root,
        session_id="active-session",
        max_rounds=3,
    )
    calls = 0

    def apply_once(**kwargs):
        nonlocal calls
        calls += 1
        return _no_op(_sha(kwargs["accepted_path"]))

    monkeypatch.setattr(service, "apply_once_schematic_refinement", apply_once)
    with pytest.raises(UserError, match="already reserved") as exc_info:
        service.refine_schematic(
            accepted_path=accepted,
            runtime=runtime,
            session_id="active-session",
        )

    assert exc_info.value.code == "REFINEMENT_EVIDENCE_EXISTS"
    assert calls == 0
    assert accepted.read_bytes() == b"A"
    assert existing.reservation_path.exists()


def test_stale_reservation_is_not_stolen_or_replaced(monkeypatch, tmp_path: Path) -> None:
    accepted = tmp_path / "accepted.kicad_sch"
    accepted.write_bytes(b"A")
    runtime = _runtime(tmp_path)
    reservation_path = (
        runtime.evidence_root / "sessions" / ".reservations" / "stale-session.lock"
    )
    reservation_path.parent.mkdir(parents=True)
    reservation_path.write_text("orphaned-owner\n", encoding="ascii")
    calls = 0

    def apply_once(**kwargs):
        nonlocal calls
        calls += 1
        return _no_op(_sha(kwargs["accepted_path"]))

    monkeypatch.setattr(service, "apply_once_schematic_refinement", apply_once)
    with pytest.raises(UserError, match="stale reservation state") as exc_info:
        service.refine_schematic(
            accepted_path=accepted,
            runtime=runtime,
            session_id="stale-session",
        )

    assert exc_info.value.code == "REFINEMENT_EVIDENCE_EXISTS"
    assert calls == 0
    assert accepted.read_bytes() == b"A"
    assert reservation_path.read_text(encoding="ascii") == "orphaned-owner\n"


def test_failed_session_releases_lock_only_after_durable_failure_evidence(
    monkeypatch, tmp_path: Path
) -> None:
    accepted = tmp_path / "accepted.kicad_sch"
    accepted.write_bytes(b"A")
    runtime = _runtime(tmp_path)
    calls = 0

    def apply_once(**kwargs):
        nonlocal calls
        calls += 1
        raise UserError("provider failed", code="LLM_PROVIDER_FAILED")

    monkeypatch.setattr(service, "apply_once_schematic_refinement", apply_once)
    with pytest.raises(UserError, match="provider failed"):
        service.refine_schematic(
            accepted_path=accepted,
            runtime=runtime,
            session_id="failed-session",
        )

    reservation = runtime.evidence_root / "sessions" / ".reservations" / "failed-session.lock"
    session_evidence = runtime.evidence_root / "sessions" / "failed-session"
    assert calls == 1
    assert accepted.read_bytes() == b"A"
    assert session_evidence.is_dir()
    assert not reservation.exists()

    with pytest.raises(UserError, match="session evidence already exists") as exc_info:
        service.refine_schematic(
            accepted_path=accepted,
            runtime=runtime,
            session_id="failed-session",
        )
    assert exc_info.value.code == "REFINEMENT_EVIDENCE_EXISTS"
    assert calls == 1


def test_terminal_evidence_failure_retains_reservation_and_blocks_replay(
    monkeypatch, tmp_path: Path
) -> None:
    accepted = tmp_path / "accepted.kicad_sch"
    accepted.write_bytes(b"A")
    runtime = _runtime(tmp_path)
    calls = 0

    def apply_once(**kwargs):
        nonlocal calls
        calls += 1
        return _no_op(_sha(kwargs["accepted_path"]))

    def fail_evidence(*args, **kwargs):
        raise UserError("evidence failed", code="REFINEMENT_EVIDENCE_WRITE_FAILED")

    monkeypatch.setattr(service, "apply_once_schematic_refinement", apply_once)
    monkeypatch.setattr(service, "_publish_session_evidence", fail_evidence)

    with pytest.raises(UserError, match="evidence failed"):
        service.refine_schematic(
            accepted_path=accepted,
            runtime=runtime,
            session_id="evidence-failed-session",
        )

    reservation = (
        runtime.evidence_root
        / "sessions"
        / ".reservations"
        / "evidence-failed-session.lock"
    )
    assert reservation.is_file()
    assert calls == 1
    assert accepted.read_bytes() == b"A"

    with pytest.raises(UserError, match="already reserved") as exc_info:
        service.refine_schematic(
            accepted_path=accepted,
            runtime=runtime,
            session_id="evidence-failed-session",
        )
    assert exc_info.value.code == "REFINEMENT_EVIDENCE_EXISTS"
    assert calls == 1
