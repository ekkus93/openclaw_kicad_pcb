from __future__ import annotations

from pathlib import Path

from kicad_pcb.corpus.metadata import (
    CorpusFixtureMetadata,
    detect_fixture_id_collisions,
    make_fixture_id,
    write_fixture_metadata,
)


def test_make_fixture_id_normalizes_hyphenated_names() -> None:
    assert make_fixture_id(Path("mcp2551-can-transciever.kicad_sch")) == "mcp2551-can-transciever"


def test_make_fixture_id_collapses_repeated_separators() -> None:
    assert make_fixture_id(Path("Foo !! copy.kicad_sch")) == "foo-copy"


def test_make_fixture_id_uses_hash_suffix_for_collision_groups(tmp_path: Path) -> None:
    source_dir = tmp_path / "model_kicad_files"
    source_dir.mkdir()
    path_a = source_dir / "foo.kicad_sch"
    path_b = source_dir / "Foo!.kicad_sch"
    path_a.write_text("", encoding="utf-8")
    path_b.write_text("", encoding="utf-8")

    collisions = detect_fixture_id_collisions([path_a, path_b], relative_to=source_dir)
    fixture_a = make_fixture_id(
        path_a,
        relative_to=source_dir,
        colliding_paths=collisions["foo"],
    )
    fixture_b = make_fixture_id(
        path_b,
        relative_to=source_dir,
        colliding_paths=collisions["foo"],
    )

    assert fixture_a.startswith("foo--")
    assert fixture_b.startswith("foo--")
    assert fixture_a != fixture_b


def test_metadata_json_round_trip(tmp_path: Path) -> None:
    metadata = CorpusFixtureMetadata(
        fixture_id="fixture-a",
        source_file_name="fixture-a.kicad_sch",
        source_path="model_kicad_files/fixture-a.kicad_sch",
        status="layout_only",
        symbol_count=4,
        wire_count=5,
        label_count=1,
        global_label_count=2,
        power_symbol_count=1,
        has_embedded_symbols=True,
        has_circuit_ir=False,
        requires_custom_symbols=False,
    )
    path = tmp_path / "metadata.json"
    write_fixture_metadata(metadata, path)

    loaded = CorpusFixtureMetadata.model_validate_json(path.read_text(encoding="utf-8"))
    assert loaded == metadata
