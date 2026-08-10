"""P2-A regression coverage for serialized wizard-session mutations."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from kicad_pcb_web.errors import ResourceBusyError
from kicad_pcb_web.services.resource_locks import resource_lock, resource_lock_path
from kicad_pcb_web.settings import WebSettings


def _settings(tmp_path: Path) -> WebSettings:
    return WebSettings(
        data_dir=tmp_path,
        jobs_dir=tmp_path / "jobs",
        mutation_lock_timeout_s=0.02,
    )


def test_different_wizard_sessions_use_independent_lock_files(tmp_path: Path) -> None:
    settings = _settings(tmp_path)

    first = resource_lock_path(settings, "wizard", "wiz_first")
    second = resource_lock_path(settings, "wizard", "wiz_second")

    assert first != second
    assert first.parent == second.parent


@pytest.mark.skipif(os.name != "posix", reason="Permanent CI validates POSIX flock serialization")
def test_held_wizard_session_lock_blocks_same_session_but_not_different_session(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)

    with resource_lock(settings, kind="wizard", resource_id="wiz_held"):
        with resource_lock(settings, kind="wizard", resource_id="wiz_other"):
            pass

        with (
            pytest.raises(ResourceBusyError) as exc_info,
            resource_lock(settings, kind="wizard", resource_id="wiz_held"),
        ):
            pytest.fail("same-session mutation unexpectedly acquired a second lock")

    assert exc_info.value.code == "RESOURCE_BUSY"
    assert exc_info.value.retryable is True
    assert exc_info.value.details == {
        "resource_id": "wiz_held",
        "resource_kind": "wizard",
    }
