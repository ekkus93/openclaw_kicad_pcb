# Model KiCad corpus workflow

This workflow is a deterministic regression harness for the schematic generator. It is **not** ML training.

## End-to-end loop

1. Add one or more raw `.kicad_sch` files under `model_kicad_files/`.
2. Ingest them into stable fixtures:

   ```bash
   uv run python -m kicad_pcb.cli model-corpus ingest \
     --source-dir model_kicad_files \
     --out-dir tests/fixtures/model_corpus \
     --refresh
   ```

3. Review the committed fixture artifacts under `tests/fixtures/model_corpus/`.
4. Evaluate fixtures that have `circuit_ir.json`:

   ```bash
   uv run python -m kicad_pcb.cli model-corpus evaluate \
     --corpus-dir tests/fixtures/model_corpus \
     --out-dir code_review/generated/model_eval
   ```

5. Read `summary.md` plus each fixture's `actionable_failures.md`.
6. Improve generic generator/evaluator rules.
7. Re-run validation:

   ```bash
   uv run ruff check .
   uv run mypy src/kicad_pcb src/kicad_pcb_web
   uv run pytest
   ```

## What ingest writes

Each fixture directory may contain:

- `source.kicad_sch`
- `source_normalized.kicad_sch` as the KiCad-loader-facing normalization copy
- `metadata.json`
- `source_layout_features.json`
- `source_embedded_symbols.sexpr`
- `source_netlist.kicadxml` when KiCad XML export succeeds
- `circuit_ir.json` when XML export/import succeeds

If repo-compatible KiCad CLI is unavailable, ingestion still writes partial fixtures and marks them `pending_netlist_export` or `layout_only`. When KiCad is available, ingest now preserves the raw source schematic and writes a deterministic `source_normalized.kicad_sch` copy for `kicad-cli sch export netlist`.

## What evaluate measures

- **Electrical equivalence**: compares exported/generated `CircuitIR` connectivity against the source fixture's `CircuitIR`.
- **Intrinsic quality**: scores the generated schematic on validity, spread, power-symbol usage, routing simplicity, and layout-lint pressure.
- **Source similarity**: compares layout style signals such as role counts, relative positions, label strategy, geometry spread, normalized 3×3 placement zones, and explicit 90-degree orientation matches without comparing exact coordinates. Missing expected generated references count as mismatches; metrics with no applicable source symbols are reported as not applicable rather than receiving free credit.

Electrical equivalence is the hard gate. Layout quality and source similarity explain *how* the generator diverged.

## Current environment caveat

The corpus/export workflow now targets `kicad-cli >= 9.0.0`. On the current `kicad-cli 9.0.9` machine:

- ingest now succeeds for the current imported source schematics by exporting from `source_normalized.kicad_sch`, and the committed corpus fixtures now include `source_netlist.kicadxml` plus `circuit_ir.json`
- evaluate is no longer blocked on missing source electrical artifacts, but the current full-corpus run now fails later in generation on at least one fixture because symbol resolution cannot find `SamacSys_Parts:ULQ2003AQDRQ1`
- the default `uv run pytest` suite stays green, and the KiCad-marked suite now expects KiCad 9 specifically

## Fixture and generated-output policy

`tests/fixtures/model_corpus/` contains curated, reviewable inputs and expected
source artifacts. Raw `source.kicad_sch` files are immutable; loader normalization
is written separately as `source_normalized.kicad_sch`. Rejected inputs remain in
ingestion reports rather than becoming empty fixture directories.

`code_review/generated/model_eval/` is the canonical regenerable output directory.
Full generated projects, copied symbol libraries, evaluation reports, feature JSON,
and preview images from that directory are ignored and must not be committed. CI
runs `scripts/check-generated-tree.sh` to prevent generated output from reappearing
under current or archived review directories. Keep human-authored review conclusions
and provenance/license records; do not commit artifacts that can be recreated from
the curated fixtures. RESTART1 removes current-tree generated copies only and does
not rewrite Git history.
