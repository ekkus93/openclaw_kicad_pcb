"""Injectable adapters for subprocess and filesystem side effects.

This module provides:

* :class:`RunResult` — typed subprocess result replacing ``CompletedProcess``.
* :class:`RunnerProtocol` / :class:`SubprocessRunner` / :class:`FakeRunner` —
  thin injection seam around ``subprocess.run``.
* :class:`FsProtocol` / :class:`RealFs` / :class:`FakeFs` —
  thin injection seam around ``pathlib.Path`` filesystem operations.
* :class:`KicadCliAdapter` — typed, injectable wrapper for all ``kicad-cli``
  sub-commands used by the skill.

Inject :class:`FakeRunner` and :class:`FakeFs` into :class:`KicadCliAdapter`
for unit testing without a real KiCad installation or real filesystem::

    fake_runner = FakeRunner(
        {"pcb drc": RunResult(0, "", "")},
        files={"/tmp/proj/drc_report.json": '{"violations": []}'},
    )
    cli = KicadCliAdapter(runner=fake_runner, fs=fake_runner.fs)

Implementation is split across:
* :mod:`kicad_pcb._adapters_runner` — :class:`RunResult`, runner classes
* :mod:`kicad_pcb._adapters_fs`     — filesystem protocol and implementations
* :mod:`kicad_pcb._adapters_cli`    — :class:`KicadCliAdapter`
"""

from __future__ import annotations

from ._adapters_cli import KicadCliAdapter  # noqa: F401
from ._adapters_fs import FakeFs, FsProtocol, RealFs  # noqa: F401
from ._adapters_runner import FakeRunner, RunnerProtocol, RunResult, SubprocessRunner  # noqa: F401

__all__ = [
    "FakeFs",
    "FakeRunner",
    "FsProtocol",
    "KicadCliAdapter",
    "RealFs",
    "RunnerProtocol",
    "RunResult",
    "SubprocessRunner",
]
