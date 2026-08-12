from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

from kicad_pcb.errors import UserError
from kicad_pcb.refinement.session_reservation import (
    RefinementSessionReservation,
    release_refinement_session_reservation,
    reserve_refinement_session_namespace,
)


def test_concurrent_session_reservation_has_exactly_one_owner(tmp_path: Path) -> None:
    root = tmp_path / "evidence"
    barrier = Barrier(2)

    def attempt() -> RefinementSessionReservation | str:
        barrier.wait()
        try:
            return reserve_refinement_session_namespace(
                root,
                session_id="concurrent-session",
                max_rounds=3,
            )
        except UserError as exc:
            return str(exc.code)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(executor.map(lambda _: attempt(), range(2)))

    reservations = tuple(
        result for result in results if isinstance(result, RefinementSessionReservation)
    )
    failures = tuple(result for result in results if isinstance(result, str))

    assert len(reservations) == 1
    assert failures == ("REFINEMENT_EVIDENCE_EXISTS",)
    assert reservations[0].reservation_path.is_file()

    release_refinement_session_reservation(reservations[0])
    assert not reservations[0].reservation_path.exists()
