# Responses 1

This file captures the open questions and conflicts found while reviewing:

- `docs/WEB_APP_CODE_REVIEW_FIX_SPEC.md`
- `docs/WEB_APP_CODE_REVIEW_FIX_TODO.md`

## Confirmed issues with no decision needed

These review points appear valid against the current `webapp` branch:

1. Web generation still defaults to `validation="kicad"` in `src/kicad_pcb_web/schemas.py`.
2. Empty symbol search currently returns `422`, not `400`, because `src/kicad_pcb_web/routes/api_symbols.py` uses `Query(..., min_length=1)`.
3. The web app only maps `UserError`; broader `KiCadError` / `ToolError` handling is missing.
4. `job.json` is currently written into `artifacts/` and is therefore publicly downloadable.
5. `job_detail.html` still mostly dumps raw JSON instead of surfacing warnings/diagnostics as separate sections.
6. The Generate button does not render direct artifact links.
7. `pyproject.toml` does not currently include bundled `resources/symbols/*.kicad_sym` as package data.
8. There are stale leftovers under `kicad-pcb/`, including `kicad-pcb/tests/` and old `*.egg-info` directories.
9. The new review spec/TODO files currently exist under `docs/`, not at repo root.

## Questions that need clarification

### 1. Should `job.json` remain publicly downloadable?

There is a direct conflict between the earlier migration decision and the new review docs.

Earlier approved behavior:

```text
canonical:   data/jobs/<job_id>/job.json
downloadable: data/jobs/<job_id>/artifacts/job.json
```

New review docs now require:

```text
keep only private canonical metadata at data/jobs/<job_id>/job.json
do not copy job.json into artifacts/
do not allow /api/jobs/<job_id>/artifacts/job.json
```

**Question:** Which rule should win?

### 2. Should the Phase 7 regression-threshold changes be reverted?

The new review docs say the threshold loosenings in:

```text
tests/unit/test_phase7_regression_guardrails.py
```

should be reverted unless explicitly justified.

Those changes were previously made to restore a green baseline after observed output-shape drift, not as arbitrary web-app-only changes.

**Question:** Should these threshold changes be reverted now, or should they stay with an explicit rationale added to the code/docs?

### 3. Should the new review docs be moved to the repo root?

The TODO says these files should exist at repo root:

```text
WEB_APP_CODE_REVIEW_FIX_SPEC.md
WEB_APP_CODE_REVIEW_FIX_TODO.md
```

but they currently exist only under:

```text
docs/WEB_APP_CODE_REVIEW_FIX_SPEC.md
docs/WEB_APP_CODE_REVIEW_FIX_TODO.md
```

This is the same type of doc-location issue that came up earlier with the migration docs.

**Question:** Should these two files be moved/copied to repo root as part of this fix pass?

### 4. How strong should the README web-app framing be?

The new review spec asks for wording equivalent to:

```text
This branch provides a Python FastAPI web app for deterministic KiCad project generation from Circuit IR JSON.
The web app does not require OpenClaw or an LLM.
OpenClaw skill files are archived under legacy/openclaw-skill for reference.
```

The README intro was already refreshed, but not yet with this exact stronger wording.

**Question:** Should the README be updated to explicitly state all three points above, or is the current lighter framing acceptable?
