# Model KiCad source files

This directory contains raw source `.kicad_sch` examples used to build the deterministic model-corpus fixtures under `tests/fixtures/model_corpus/`.

## Expectations

- Keep the original source schematics here.
- Do not hand-edit generated fixture artifacts under `tests/fixtures/model_corpus/`, except for curated metadata fields such as `license`, `source_url`, or `notes` when needed.
- Record source provenance in fixture metadata after ingestion:
  - `license`
  - `source_url`
  - optional review notes

## Ingesting files

From the repo root:

```bash
uv run python -m kicad_pcb.cli model-corpus ingest \
  --source-dir model_kicad_files \
  --out-dir tests/fixtures/model_corpus \
  --refresh
```

The corpus workflow now targets **KiCad 9**. On the current machine `kicad-cli 9.0.9` is available, but the imported source schematics still fall back to partial `layout_only` fixtures because `kicad-cli sch export netlist` cannot load these raw source files yet.
