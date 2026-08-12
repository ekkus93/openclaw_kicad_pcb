from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from kicad_pcb.errors import UserError
from kicad_pcb.refinement.transaction import CandidateState, SchematicCandidateTransaction


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_rejected_candidate_leaves_accepted_bytes_unchanged(tmp_path: Path) -> None:
    accepted = tmp_path / "design.kicad_sch"
    accepted.write_bytes(b"accepted")
    starting = accepted.read_bytes()

    with SchematicCandidateTransaction(accepted) as transaction:
        transaction.candidate_path.write_bytes(b"bad candidate")
        transaction.reject()

    assert accepted.read_bytes() == starting
    assert transaction.state is CandidateState.REJECTED


def test_exact_validated_candidate_is_promoted_atomically(tmp_path: Path) -> None:
    accepted = tmp_path / "design.kicad_sch"
    accepted.write_bytes(b"accepted")

    with SchematicCandidateTransaction(accepted) as transaction:
        transaction_root = transaction.candidate_path.parent
        assert transaction_root != accepted.parent
        transaction.candidate_path.write_bytes(b"validated candidate")
        candidate_hash = _hash(transaction.candidate_path)
        transaction.mark_validated(candidate_hash=candidate_hash)
        promoted_hash = transaction.promote()

    assert promoted_hash == candidate_hash
    assert accepted.read_bytes() == b"validated candidate"
    assert transaction.state is CandidateState.PROMOTED
    assert not transaction_root.exists()


def test_candidate_transaction_removes_nested_render_scratch(tmp_path: Path) -> None:
    accepted = tmp_path / "design.kicad_sch"
    accepted.write_bytes(b"accepted")

    with SchematicCandidateTransaction(accepted) as transaction:
        transaction_root = transaction.candidate_path.parent
        assert transaction_root != accepted.parent
        render_scratch = transaction_root / "post-edit-render"
        render_scratch.mkdir()
        (render_scratch / "schematic.svg").write_text("<svg/>", encoding="utf-8")
        (render_scratch / "schematic.png").write_bytes(b"png")
        assert render_scratch.is_dir()

    assert not transaction_root.exists()
    assert not render_scratch.exists()
    assert accepted.exists()


def test_candidate_change_after_validation_is_rejected(tmp_path: Path) -> None:
    accepted = tmp_path / "design.kicad_sch"
    accepted.write_bytes(b"accepted")
    starting = accepted.read_bytes()

    with SchematicCandidateTransaction(accepted) as transaction:
        transaction.candidate_path.write_bytes(b"validated candidate")
        transaction.mark_validated(candidate_hash=_hash(transaction.candidate_path))
        transaction.candidate_path.write_bytes(b"tampered")
        with pytest.raises(UserError, match="changed before promotion"):
            transaction.promote()

    assert accepted.read_bytes() == starting
    assert transaction.state is CandidateState.FAILED


def test_stale_accepted_file_cannot_be_replaced(tmp_path: Path) -> None:
    accepted = tmp_path / "design.kicad_sch"
    accepted.write_bytes(b"accepted")

    with SchematicCandidateTransaction(accepted) as transaction:
        transaction.candidate_path.write_bytes(b"candidate")
        transaction.mark_validated(candidate_hash=_hash(transaction.candidate_path))
        accepted.write_bytes(b"newer accepted")
        with pytest.raises(UserError, match="changed after candidate creation"):
            transaction.promote()

    assert accepted.read_bytes() == b"newer accepted"
    assert transaction.state is CandidateState.FAILED
