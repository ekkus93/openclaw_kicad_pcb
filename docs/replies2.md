# Replies for Copilot

These answers resolve the open questions from `responses2(15).md` regarding the model KiCad corpus workflow.

## 1. Deterministic rule for `make_fixture_id(...)` slug collisions

Use this rule:

```text
If a normalized slug is unique:
    fixture_id = base_slug

If two or more source files normalize to the same slug:
    fixture_id = base_slug--<hash8>
```

Where:

```text
hash8 = first 8 lowercase hex characters of sha256(normalized_relative_source_path)
```

Do **not** use `_2`, `_3`, etc. Those depend on scan order and can change when new files are added.

Required behavior:

```text
foo.kicad_sch        → foo
Foo!.kicad_sch       → foo--a1b2c3d4
foo copy.kicad_sch   → foo-copy
```

If two files both normalize to `foo`, both should use hash suffixes in a fresh rebuild:

```text
foo.kicad_sch        → foo--11112222
Foo!.kicad_sch       → foo--33334444
```

This avoids ambiguous ownership of the unsuffixed `foo` fixture.

Implementation details:

```text
- Normalize filename stem to lowercase ASCII.
- Replace non-alphanumeric runs with `-`.
- Trim leading/trailing `-`.
- If empty, use `schematic`.
- Detect collisions across the complete source scan before writing fixtures.
- For collision groups, append `--<hash8>`.
```

## 2. Source of truth for `license`, `source_url`, and `notes`

Use safe defaults during automatic ingestion:

```json
{
  "license": "unknown",
  "source_url": null,
  "notes": []
}
```

The ingestion tool may optionally extract hints from comments in the `.kicad_sch`, but extracted hints must be marked as hints, not authoritative.

Recommended metadata shape:

```json
{
  "license": "unknown",
  "source_url": null,
  "notes": [],
  "detected_license_hint": null,
  "detected_source_url_hint": null,
  "metadata_review_required": true
}
```

Rules:

```text
- Do not guess license.
- Do not infer license from repository host alone.
- Do not treat comments as legally authoritative unless a human later confirms them.
- Allow developers to hand-edit metadata.json after ingestion.
- Preserve hand-edited metadata on refresh unless `--overwrite-metadata` is explicitly passed.
```

So yes: automatic ingestion should default to `unknown` / `null` / `[]`, with optional detected hints.

## 3. Representation of rejected source files

For V0, create **no fixture directory** for rejected files.

Rejected files should appear only in:

```text
tests/fixtures/model_corpus/ingestion_report.json
tests/fixtures/model_corpus/summary.md
```

Reason: fixture directories should mean “this source is usable as a corpus fixture.” Creating fixture directories for rejected files will make tests, evaluation loops, and developer expectations messier.

Required report entry:

```json
{
  "source_path": "model_kicad_files/bad-file.kicad_sch",
  "status": "rejected",
  "reason": "parse_error",
  "message": "Failed to parse KiCad schematic S-expression",
  "fixture_id": null
}
```

Allowed statuses:

```text
accepted
partial
rejected
```

Only `accepted` and `partial` get fixture directories.

## 4. V0 embedded-symbol artifact target

For V0, use the simpler deterministic fallback artifact:

```text
source_embedded_symbols.sexpr
```

Do **not** require generation of a valid standalone `.kicad_sym` file in V0.

Reason: extracting embedded symbols into a real `.kicad_sym` library is useful later, but it adds KiCad-library-format complexity that is not required for the first corpus/evaluation loop.

V0 fixture layout should allow:

```text
source.kicad_sch
metadata.json
source_layout_features.json
source_embedded_symbols.sexpr
```

Later V1 can add:

```text
fixture_symbols.kicad_sym
```

Acceptance rule for V0:

```text
The embedded-symbol artifact must be deterministic, parseable by the project’s own S-expression parser, and sufficient for later symbol-pin extraction work.
```

## 5. Which generated outputs should be committed

Yes:

```text
tests/fixtures/model_corpus/ = committed test data
code_review/generated/model_eval/ = uncommitted generated review output
```

Add this to `.gitignore` if it is not already covered:

```gitignore
code_review/generated/model_eval/
```

The committed corpus should include deterministic fixture artifacts:

```text
tests/fixtures/model_corpus/<fixture_id>/
  source.kicad_sch
  metadata.json
  source_layout_features.json
  source_embedded_symbols.sexpr
  circuit_ir.json              # only when available/deterministic
  source_netlist.kicadxml      # optional, if produced by kicad-cli and intentionally committed
```

But evaluation outputs should not be committed by default:

```text
code_review/generated/model_eval/<fixture_id>/
  generated project files
  evaluation_report.json
  actionable_failures.md
  previews
  logs
```

Exception: if a specific evaluation report is intentionally promoted as a regression artifact, put it somewhere under `tests/fixtures/...`, not under `code_review/generated/...`.

## 6. Preserve `OpenClaw_Managed.kicad_sch` or normalize to `generated.kicad_sch`?

Preserve the current generator behavior internally.

The generator may continue to produce:

```text
OpenClaw_Managed.kicad_sch
```

The evaluator should normalize by copying or symlinking the generated managed sheet into the evaluation output as:

```text
generated.kicad_sch
```

Recommended evaluation output:

```text
code_review/generated/model_eval/<fixture_id>/
  generated_project/
    <actual generator output files>
    OpenClaw_Managed.kicad_sch
  generated.kicad_sch
  evaluation_report.json
  actionable_failures.md
```

Rules:

```text
- Do not force the generator to change its managed-sheet naming just for evaluation.
- The evaluator should expose a stable canonical path named `generated.kicad_sch`.
- Tests should use the canonical evaluator path.
- Generator-specific tests may still assert `OpenClaw_Managed.kicad_sch`.
```

This avoids churn in the existing generation path while giving the corpus evaluator a stable file name.

## Summary decisions

```text
1. Collision IDs:
   Use base_slug if unique; use base_slug--hash8 for every member of a collision group.

2. Metadata:
   Default to license="unknown", source_url=null, notes=[].
   Preserve human edits. Optional detected hints are non-authoritative.

3. Rejected files:
   No fixture directory. Record them in ingestion_report.json and summary.md only.

4. Embedded symbols:
   V0 uses source_embedded_symbols.sexpr.
   Real fixture-local .kicad_sym can be V1.

5. Committed outputs:
   Commit tests/fixtures/model_corpus/.
   Do not commit code_review/generated/model_eval/.

6. Generated schematic naming:
   Preserve OpenClaw_Managed.kicad_sch internally.
   Evaluator also exposes canonical generated.kicad_sch.
```
