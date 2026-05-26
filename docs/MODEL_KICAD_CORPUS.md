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
- `metadata.json`
- `source_layout_features.json`
- `source_embedded_symbols.sexpr`
- `source_netlist.kicadxml` when KiCad XML export succeeds
- `circuit_ir.json` when XML export/import succeeds

If repo-compatible KiCad CLI is unavailable, ingestion still writes partial fixtures and marks them `pending_netlist_export` or `layout_only`.

## What evaluate measures

- **Electrical equivalence**: compares exported/generated `CircuitIR` connectivity against the source fixture's `CircuitIR`.
- **Intrinsic quality**: scores the generated schematic on validity, spread, power-symbol usage, routing simplicity, and layout-lint pressure.
- **Source similarity**: compares layout style signals such as role counts, relative positions, label strategy, and geometry spread without comparing exact coordinates.

Electrical equivalence is the hard gate. Layout quality and source similarity explain *how* the generator diverged.

## Current environment caveat

The repository's symbol-bearing schematic fixtures need `kicad-cli >= 8.0.0` for XML netlist export. On the current `kicad-cli 7.0.11` machine:

- ingest works and creates committed partial fixtures
- evaluate runs, but skips real fixtures that do not yet have `circuit_ir.json`
- the default `uv run pytest` suite stays green because KiCad-required integration tests skip cleanly on unsupported versions
