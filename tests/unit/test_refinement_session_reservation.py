from __future__ import annotations

from pathlib import Path

import pytest

from kicad_pcb.errors import UserError
from kicad_pcb.refinement.session_reservation import (
    release_refinement_session_reservation,
    reserve_refinement_session_namespace,
)


def test_session_namespace_reservation_is_exclusive_and_releasable(tmp_path: Path) -> None:
    root = tmp_path / "evidence"
    reservation = reserve_refinement_session_namespace(
        root,
        session_id="session-001",
        max_rounds=3,
    )

    assert reservation.reservation_path.is_file()
    assert reservation.reservation_path.read_text(encoding="ascii") == reservation.token + "\n"
    with pytest.raises(UserError, match="already reserved") as exc_info:
        reserve_refinement_session_namespace(
            root,
            session_id="session-001",
            max_rounds=3,
        )
    assert exc_info.value.code == "REFINEMENT_EVIDENCE_EXISTS"

    release_refinement_session_reservation(reservation)
    assert not reservation.reservation_path.exists()

    replacement = reserve_refinement_session_namespace(
        root,
        session_id="session-001",
        max_rounds=3,
    )
    release_refinement_session_reservation(replacement)


def test_session_namespace_reservation_rejects_existing_session_bundle(tmp_path: Path) -> None:
    root = tmp_path / "evidence"
    existing = root / "sessions" / "session-001"
    existing.mkdir(parents=True)

    with pytest.raises(UserError, match="session evidence already exists") as exc_info:
        reserve_refinement_session_namespace(
            root,
            session_id="session-001",
            max_rounds=3,
        )

    assert exc_info.value.code == "REFINEMENT_EVIDENCE_EXISTS"


def test_session_namespace_reservation_rejects_orphan_iteration_bundle(tmp_path: Path) -> None:
    root = tmp_path / "evidence"
    (root / "session-001-round-002").mkdir(parents=True)

    with pytest.raises(UserError, match="iteration evidence already exists") as exc_info:
        reserve_refinement_session_namespace(
            root,
            session_id="session-001",
            max_rounds=3,
        )

    assert exc_info.value.code == "REFINEMENT_EVIDENCE_EXISTS"


def test_session_reservation_refuses_to_remove_foreign_or_tampered_lock(tmp_path: Path) -> None:
    root = tmp_path / "evidence"
    reservation = reserve_refinement_session_namespace(
        root,
        session_id="session-001",
        max_rounds=3,
    )
    reservation.reservation_path.write_text("different-owner\n", encoding="ascii")

    with pytest.raises(UserError, match="ownership changed") as exc_info:
        release_refinement_session_reservation(reservation)

    assert exc_info.value.code == "REFINEMENT_EVIDENCE_RESERVATION_LOST"
    assert reservation.reservation_path.exists()


def test_session_namespace_reservation_rejects_unsafe_id_and_bad_round_limit(
    tmp_path: Path,
) -> None:
    root = tmp_path / "evidence"
    with pytest.raises(UserError, match="Invalid schematic refinement session id"):
        reserve_refinement_session_namespace(
            root,
            session_id="../escape",
            max_rounds=3,
        )
    with pytest.raises(ValueError, match="max_rounds"):
        reserve_refinement_session_namespace(
            root,
            session_id="session-001",
            max_rounds=0,
        )
