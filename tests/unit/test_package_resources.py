"""Package resource visibility tests."""

from __future__ import annotations

from importlib import resources


def test_bundled_symbol_resources_visible() -> None:
    symbol_dir = resources.files("kicad_pcb").joinpath("resources", "symbols")
    names = {path.name for path in symbol_dir.iterdir()}

    assert any(name.endswith(".kicad_sym") for name in names)
