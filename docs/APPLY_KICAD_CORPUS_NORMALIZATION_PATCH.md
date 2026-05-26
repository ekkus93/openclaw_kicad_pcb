# Instructions for Applying `kicad_corpus_normalization_fix.patch`

This patch fixes the model-corpus ingestion path for raw Internet KiCad schematic files in `model_kicad_files/`.

The main issue is that the current raw schematic files are parseable by the project’s internal S-expression parser, but some are not clean standalone inputs for `kicad-cli sch export netlist`. The patch adds a deterministic normalization step that preserves the raw schematic while creating a KiCad-loader-facing copy named `source_normalized.kicad_sch`.

## What this patch changes

The patch modifies or adds these files:

```text
src/kicad_pcb/corpus/ingestion.py
src/kicad_pcb/corpus/normalization.py
tests/unit/test_model_corpus_normalization.py
```

The new normalization behavior:

```text
source.kicad_sch               # exact raw file, preserved
source_normalized.kicad_sch    # normalized copy used for kicad-cli netlist export
source_layout_features.json
source_embedded_symbols.sexpr
metadata.json
```

The normalized copy should:

```text
- remove invalid top-level CircuitSnips-style `(comment ...)` nodes
- replace invalid root UUIDs with deterministic UUIDv5 values
- add or normalize `(sheet_instances (path "/" (page "1")))`
- rewrite placed-symbol instance paths to use the normalized root UUID
- leave the raw source schematic untouched
```

## Required starting point

Run these commands from the repository root, the directory that contains `src/`, `tests/`, and `model_kicad_files/`.

Example:

```bash
cd ~/work/openclaw_kicad_pcb-webapp
```

Check that you are in the correct directory:

```bash
test -d src/kicad_pcb && test -d tests && test -d model_kicad_files && echo "repo root OK"
```

Also check that the working tree is clean or that current changes are intentionally saved:

```bash
git status --short
```

Do not apply the patch on top of unrelated uncommitted edits unless you are intentionally merging them.

## Copy the patch into the repo root

Place the patch file at the repository root:

```text
kicad_corpus_normalization_fix.patch
```

For example:

```bash
cp /path/to/kicad_corpus_normalization_fix.patch ./kicad_corpus_normalization_fix.patch
```

## Apply the patch

The patch was generated from two sibling working directories, so when applying from the repository root, use `-p2`.

First verify it applies cleanly:

```bash
git apply -p2 --check kicad_corpus_normalization_fix.patch
```

Then apply it:

```bash
git apply -p2 kicad_corpus_normalization_fix.patch
```

Confirm the expected files changed:

```bash
git status --short
```

Expected result should include:

```text
M  src/kicad_pcb/corpus/ingestion.py
A  src/kicad_pcb/corpus/normalization.py
A  tests/unit/test_model_corpus_normalization.py
```

## If the patch does not apply

If this fails:

```bash
git apply -p2 --check kicad_corpus_normalization_fix.patch
```

try this only if you are applying from the parent directory that contains the `openclaw_kicad_pcb-webapp/` directory:

```bash
cd ..
git -C openclaw_kicad_pcb-webapp apply -p2 --check ../openclaw_kicad_pcb-webapp/kicad_corpus_normalization_fix.patch
```

Do not blindly use `--reject` unless necessary. Prefer fixing the path level first.

If the patch still fails because `ingestion.py` has changed, manually port the patch by doing the following:

```text
1. Add `src/kicad_pcb/corpus/normalization.py` from the patch.
2. Add `tests/unit/test_model_corpus_normalization.py` from the patch.
3. In `src/kicad_pcb/corpus/ingestion.py`, import:

   from .normalization import normalize_for_kicad_export, serialize_normalized_schematic

4. During per-file fixture ingestion, after copying/parsing `source.kicad_sch`, call:

   normalized = normalize_for_kicad_export(doc, fixture_id=fixture_id)

5. Write the normalized copy to:

   fixture_dir / "source_normalized.kicad_sch"

6. Pass `source_normalized.kicad_sch`, not raw `source.kicad_sch`, into the optional KiCad netlist export path.

7. Preserve the raw source file exactly as `source.kicad_sch`.

8. Append normalization changes to `status_reasons` using the prefix `normalized:`.
```

## Run tests after applying

Run the new focused tests first:

```bash
uv run pytest tests/unit/test_model_corpus_normalization.py
```

Then run corpus-related tests:

```bash
uv run pytest tests/unit -k "model_corpus or corpus or normalization"
```

Then run the full backend test suite:

```bash
uv run pytest
```

Run formatting and static checks if available in the repo:

```bash
uv run ruff check .
uv run mypy src/kicad_pcb src/kicad_pcb_web
```

If `uv` tries to download Python and fails because of network/DNS, run the equivalent commands using the local project environment.

## Run KiCad-dependent ingestion validation

This part requires `kicad-cli` 9.x or newer.

Check the installed KiCad CLI version:

```bash
kicad-cli version
```

If it is older than 9.x, do not treat failures as generator or normalization bugs. Upgrade KiCad CLI first.

Then run:

```bash
uv run python -m kicad_pcb.cli model-corpus ingest \
  --source-dir model_kicad_files \
  --out-dir tests/fixtures/model_corpus \
  --refresh \
  --require-kicad
```

Expected behavior:

```text
- each accepted fixture preserves raw `source.kicad_sch`
- each accepted fixture also writes `source_normalized.kicad_sch`
- KiCad XML netlist export uses `source_normalized.kicad_sch`
- normalization changes appear in metadata/status reasons
- rejected files are reported in ingestion_report.json and summary.md
```

## Important correctness requirements

Do not change these design choices while applying the patch:

```text
- Raw source schematics must remain untouched as `source.kicad_sch`.
- Normalization is only for KiCad-loader-facing export.
- Do not overwrite or “clean up” the raw files in `model_kicad_files/`.
- Do not special-case individual schematic filenames.
- Deterministic UUID replacement must be based on fixture identity, not random UUIDs.
- The evaluator/generator should continue to operate on generic rules, not file-specific hacks.
```

## Suggested commit message

```text
Normalize model-corpus schematics before KiCad netlist export

Add a deterministic normalization pass for raw Internet KiCad schematics used by
model-corpus ingestion. Preserve the raw source file while writing a
source_normalized.kicad_sch copy for kicad-cli export. The normalization removes
invalid top-level metadata comments, replaces invalid root UUIDs, normalizes root
sheet instances, and rewrites placed-symbol instance paths. Add focused unit
coverage for CircuitSnips-style schematic fragments.
```
