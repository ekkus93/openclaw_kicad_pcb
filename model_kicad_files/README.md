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

On this machine, `kicad-cli 7.0.11` is too old for the repo's symbol-bearing schematics, so ingest currently produces partial fixtures with `pending_netlist_export` until `kicad-cli >= 8.0.0` is available.
