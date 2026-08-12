"""Atomic reservation of refinement evidence namespaces before any model call or mutation."""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from pathlib import Path

from kicad_pcb.errors import UserError

_MAX_SESSION_ID_LENGTH = 96
_MAX_ROUNDS = 20
_ALLOWED_SESSION_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_."
)


@dataclass(frozen=True)
class RefinementSessionReservation:
    """Opaque ownership token for one refinement session evidence namespace."""

    session_id: str
    reservation_path: Path
    token: str
    max_rounds: int


def reserve_refinement_session_namespace(
    evidence_root: Path,
    *,
    session_id: str,
    max_rounds: int,
) -> RefinementSessionReservation:
    """Reserve a session ID before refinement can call a model or mutate accepted bytes."""

    _validate_session_id(session_id)
    _validate_max_rounds(max_rounds)
    sessions_root = evidence_root / "sessions"
    reservations_root = sessions_root / ".reservations"
    reservations_root.mkdir(parents=True, exist_ok=True)

    _reject_existing_evidence(evidence_root, session_id=session_id)

    token = secrets.token_hex(16)
    reservation_path = reservations_root / f"{session_id}.lock"
    try:
        fd = os.open(
            reservation_path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
    except FileExistsError as exc:
        raise UserError(
            "Refinement session id is already reserved or has stale reservation state.",
            code="REFINEMENT_EVIDENCE_EXISTS",
            details={"session_id": session_id},
        ) from exc

    try:
        with os.fdopen(fd, "w", encoding="ascii") as handle:
            handle.write(token + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        _fsync_directory(reservations_root)
        try:
            _reject_existing_evidence(evidence_root, session_id=session_id)
        except Exception:
            _remove_owned_reservation(reservation_path, token)
            raise
    except Exception:
        if reservation_path.exists():
            _remove_owned_reservation(reservation_path, token)
        raise

    return RefinementSessionReservation(
        session_id=session_id,
        reservation_path=reservation_path,
        token=token,
        max_rounds=max_rounds,
    )


def release_refinement_session_reservation(
    reservation: RefinementSessionReservation,
) -> None:
    """Release only the reservation owned by the supplied opaque token."""

    _validate_session_id(reservation.session_id)
    _validate_max_rounds(reservation.max_rounds)
    _remove_owned_reservation(reservation.reservation_path, reservation.token)


def _reject_existing_evidence(
    evidence_root: Path,
    *,
    session_id: str,
) -> None:
    if (evidence_root / "sessions" / session_id).exists():
        raise UserError(
            "Refinement session evidence already exists for this session id.",
            code="REFINEMENT_EVIDENCE_EXISTS",
            details={"session_id": session_id},
        )
    for round_number in range(1, _MAX_ROUNDS + 1):
        iteration_id = f"{session_id}-round-{round_number:03d}"
        if (evidence_root / iteration_id).exists():
            raise UserError(
                "Refinement iteration evidence already exists for this session id.",
                code="REFINEMENT_EVIDENCE_EXISTS",
                details={"session_id": session_id, "iteration_id": iteration_id},
            )


def _remove_owned_reservation(path: Path, token: str) -> None:
    try:
        actual = path.read_text(encoding="ascii")
    except FileNotFoundError as exc:
        raise UserError(
            "Refinement session reservation disappeared unexpectedly.",
            code="REFINEMENT_EVIDENCE_RESERVATION_LOST",
        ) from exc
    except OSError as exc:
        raise UserError(
            "Refinement session reservation could not be read safely.",
            code="REFINEMENT_EVIDENCE_RESERVATION_LOST",
        ) from exc
    if actual != token + "\n":
        raise UserError(
            "Refinement session reservation ownership changed unexpectedly.",
            code="REFINEMENT_EVIDENCE_RESERVATION_LOST",
        )
    try:
        path.unlink()
    except OSError as exc:
        raise UserError(
            "Refinement session reservation could not be released safely.",
            code="REFINEMENT_EVIDENCE_RESERVATION_LOST",
        ) from exc
    _fsync_directory(path.parent)


def _validate_session_id(session_id: str) -> None:
    if (
        not session_id
        or len(session_id) > _MAX_SESSION_ID_LENGTH
        or any(ch not in _ALLOWED_SESSION_CHARS for ch in session_id)
    ):
        raise UserError(
            "Invalid schematic refinement session id.",
            code="REFINEMENT_INVALID_SESSION_ID",
        )


def _validate_max_rounds(max_rounds: int) -> None:
    if type(max_rounds) is not int or not 1 <= max_rounds <= _MAX_ROUNDS:
        raise ValueError(f"max_rounds must be an integer in [1, {_MAX_ROUNDS}]")


def _fsync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
