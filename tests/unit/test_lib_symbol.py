from __future__ import annotations

from kicad_pcb.lib_symbol import read_lib_symbol_def_flat


def test_read_lib_symbol_def_flat_falls_through_to_system_library() -> None:
    """Missing repo-local power variants should fall through to later symbol dirs."""
    sym_def = read_lib_symbol_def_flat("power", "+5V", symbols_dir=None)

    assert sym_def is not None


def test_read_lib_symbol_def_flat_preserves_repo_local_gnd_power_metadata() -> None:
    sym_def = read_lib_symbol_def_flat("power", "GND", symbols_dir=None)

    assert sym_def is not None
    assert any(getattr(item, "key", None) == "power" for item in sym_def.items)
    assert any(getattr(item, "key", None) == "property" for item in sym_def.items)
    assert any(getattr(item, "key", None) == "symbol" for item in sym_def.items)


def test_read_lib_symbol_def_flat_preserves_repo_local_vcc_power_metadata() -> None:
    sym_def = read_lib_symbol_def_flat("power", "VCC", symbols_dir=None)

    assert sym_def is not None
    assert any(getattr(item, "key", None) == "power" for item in sym_def.items)
    assert any(getattr(item, "key", None) == "property" for item in sym_def.items)
    assert any(getattr(item, "key", None) == "symbol" for item in sym_def.items)
