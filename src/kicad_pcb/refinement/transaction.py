"""Transactional candidate lifecycle for schematic visual refinement."""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import tempfile
from enum import StrEnum
from pathlib import Path

from kicad_pcb.errors import ErrorCode, UserError

LOGGER = logging.getLogger(__name__)


class CandidateState(StrEnum):
    """Explicit candidate lifecycle."""

    CREATED = "created"
    VALIDATED = "validated"
    PROMOTED = "promoted"
    REJECTED = "rejected"
    FAILED = "failed"


class SchematicCandidateTransaction:
    """Isolate candidate bytes and promote only the exact validated artifact."""

    def __init__(
        self,
        accepted_path: Path,
        *,
        expected_accepted_hash: str | None = None,
    ) -> None:
        self.accepted_path = accepted_path
        self.starting_accepted_hash = _sha256_file(accepted_path)
        if (
            expected_accepted_hash is not None
            and expected_accepted_hash != self.starting_accepted_hash
        ):
            raise UserError(
                "Accepted schematic changed before candidate transaction creation.",
                code="REFINEMENT_STALE",
                details={
                    "expected_hash": expected_accepted_hash,
                    "actual_hash": self.starting_accepted_hash,
                },
            )

        self.transaction_root = Path(
            tempfile.mkdtemp(
                prefix=f".{accepted_path.stem}.refinement.",
                dir=accepted_path.parent,
            )
        )
        self.candidate_path = self.transaction_root / accepted_path.name
        try:
            payload = accepted_path.read_bytes()
            with self.candidate_path.open("wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
        except Exception:
            self._cleanup(suppress_errors=True)
            raise
        self.state = CandidateState.CREATED
        self.validated_candidate_hash: str | None = None

    def __enter__(self) -> SchematicCandidateTransaction:
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self._cleanup(suppress_errors=exc is not None or self.state is CandidateState.PROMOTED)

    def mark_validated(self, *, candidate_hash: str) -> None:
        """Bind validation truth to the exact current candidate bytes."""

        self._require_state(CandidateState.CREATED)
        actual = _sha256_file(self.candidate_path)
        if actual != candidate_hash:
            self.state = CandidateState.FAILED
            raise UserError(
                "Candidate bytes changed between validation and validation binding.",
                code="REFINEMENT_CANDIDATE_HASH_MISMATCH",
                details={"reported_hash": candidate_hash, "actual_hash": actual},
            )
        self.validated_candidate_hash = actual
        self.state = CandidateState.VALIDATED

    def reject(self) -> None:
        if self.state is CandidateState.PROMOTED:
            raise UserError(
                "Promoted candidate cannot be rejected retroactively.",
                code=ErrorCode.VALIDATION_FAILED,
            )
        self.state = CandidateState.REJECTED
        self._cleanup()

    def promote(self) -> str:
        """Atomically replace accepted schematic with exact validated bytes."""

        self._require_state(CandidateState.VALIDATED)
        assert self.validated_candidate_hash is not None
        current_candidate_hash = _sha256_file(self.candidate_path)
        if current_candidate_hash != self.validated_candidate_hash:
            self.state = CandidateState.FAILED
            raise UserError(
                "Validated candidate changed before promotion.",
                code="REFINEMENT_CANDIDATE_HASH_MISMATCH",
                details={
                    "validated_hash": self.validated_candidate_hash,
                    "current_hash": current_candidate_hash,
                },
            )

        current_accepted_hash = _sha256_file(self.accepted_path)
        if current_accepted_hash != self.starting_accepted_hash:
            self.state = CandidateState.FAILED
            raise UserError(
                "Accepted schematic changed after candidate creation; refusing stale promotion.",
                code="REFINEMENT_STALE",
                details={
                    "starting_hash": self.starting_accepted_hash,
                    "current_hash": current_accepted_hash,
                },
            )

        try:
            self.candidate_path.replace(self.accepted_path)
            _fsync_directory(self.accepted_path.parent)
        except OSError:
            self.state = CandidateState.FAILED
            raise
        self.state = CandidateState.PROMOTED
        promoted_hash = _sha256_file(self.accepted_path)
        if promoted_hash != self.validated_candidate_hash:
            self.state = CandidateState.FAILED
            raise UserError(
                "Promoted schematic bytes do not match the validated candidate.",
                code="REFINEMENT_CANDIDATE_HASH_MISMATCH",
                details={
                    "validated_hash": self.validated_candidate_hash,
                    "promoted_hash": promoted_hash,
                },
            )
        return promoted_hash

    def _require_state(self, required: CandidateState) -> None:
        if self.state is not required:
            raise UserError(
                f"Candidate operation requires state {required.value}, got {self.state.value}.",
                code=ErrorCode.VALIDATION_FAILED,
                details={"required_state": required.value, "actual_state": self.state.value},
            )

    def _cleanup(self, *, suppress_errors: bool = False) -> None:
        try:
            shutil.rmtree(self.transaction_root)
        except FileNotFoundError:
            return
        except OSError as exc:
            if suppress_errors:
                LOGGER.error(
                    "failed to clean up schematic refinement transaction",
                    extra={
                        "path": str(self.transaction_root),
                        "error_type": type(exc).__name__,
                    },
                )
                return
            raise UserError(
                "Failed to clean up schematic refinement transaction.",
                code=ErrorCode.IO_ERROR,
                details={
                    "path": str(self.transaction_root),
                    "reason": str(exc),
                },
            ) from exc


def accepted_path_for_refinement_candidate(candidate_path: Path) -> Path | None:
    """Resolve the canonical accepted schematic for a transaction candidate path."""

    transaction_root = candidate_path.parent
    expected_prefix = f".{candidate_path.stem}.refinement."
    if not transaction_root.name.startswith(expected_prefix):
        return None
    accepted_path = transaction_root.parent / candidate_path.name
    if not accepted_path.is_file():
        raise UserError(
            "Refinement transaction candidate has no canonical accepted baseline.",
            code="REFINEMENT_STALE",
        )
    return accepted_path


def _sha256_file(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise UserError(
            f"Failed to hash schematic candidate: {path}",
            code=ErrorCode.IO_ERROR,
            details={"path": str(path), "reason": str(exc)},
        ) from exc


def _fsync_directory(path: Path) -> None:
    try:
        fd = os.open(str(path), os.O_RDONLY)
    except OSError as exc:
        LOGGER.warning(
            "failed to open refinement directory for fsync",
            extra={"path": str(path), "error_type": type(exc).__name__},
        )
        return
    try:
        os.fsync(fd)
    except OSError as exc:
        LOGGER.warning(
            "failed to fsync refinement directory after atomic promotion",
            extra={"path": str(path), "error_type": type(exc).__name__},
        )
    finally:
        os.close(fd)
