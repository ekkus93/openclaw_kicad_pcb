# Responses 2

This file captures the open questions and ambiguities found while reviewing:

- `docs/MODEL_KICAD_CORPUS_SPEC.md`
- `docs/MODEL_KICAD_CORPUS_TODO.md`

## Questions that need clarification

### 1. What exact deterministic rule should `make_fixture_id(...)` use for slug collisions?

The docs require deterministic collision handling, but they do not define the exact policy when two different source filenames normalize to the same slug.

Examples of possible rules:

```text
base_slug
base_slug_2
base_slug_3
```

or:

```text
base_slug_<short_hash>
```

**Question:** Which collision rule should be the required behavior?

### 2. What is the source of truth for metadata fields like `license` and `source_url`?

The spec requires these `metadata.json` fields:

```json
"license": "unknown",
"source_url": null,
"notes": []
```

but the ingestion workflow does not define where those values come from for raw `.kicad_sch` files.

The safe default would be:

```text
license = "unknown"
source_url = null
notes = []
```

**Question:** Should ingestion always use those defaults unless a developer hand-edits metadata later, or is there another intended workflow?

### 3. How should rejected source files be represented?

The spec says raw inputs can become accepted fixtures, partial fixtures, or rejected fixtures.

However, the acceptance criteria only promise one fixture directory per **parseable** source file.

That leaves two possible behaviors for a rejected source file:

1. Create no fixture directory and report the rejection only in `ingestion_report.json` / `summary.md`.
2. Create a fixture directory with `metadata.json` showing `status: "rejected"`.

**Question:** Which behavior should the implementation follow?

### 4. Which V0 embedded-symbol artifact should be the target?

The spec allows either:

1. A real fixture-local `.kicad_sym` file, or
2. A deterministic fallback artifact such as `source_embedded_symbols.sexpr`.

Both are allowed by the docs, but they imply different first-pass scope.

**Question:** For V0, should the implementation target a true `.kicad_sym` artifact, or should it intentionally ship with the simpler deterministic fallback artifact first?

### 5. Which generated outputs are expected to be committed to the repository?

The docs define two generated trees:

```text
tests/fixtures/model_corpus/
code_review/generated/model_eval/
```

The likely intent seems to be:

- `tests/fixtures/model_corpus/` = committed fixture data
- `code_review/generated/model_eval/` = local/generated review output

but the docs do not state that explicitly.

**Question:** Should `tests/fixtures/model_corpus/` be treated as committed test data while `code_review/generated/model_eval/` remains uncommitted generated output?

### 6. Should evaluation preserve the current managed-sheet naming, or normalize it?

The spec allows generated output to contain:

```text
generated.kicad_sch
```

or:

```text
OpenClaw_Managed.kicad_sch
```

The current generator path already uses the managed-sheet structure.

**Question:** Should evaluation preserve the current `OpenClaw_Managed.kicad_sch` behavior as-is, or should it normalize outputs to a new `generated.kicad_sch` name?
