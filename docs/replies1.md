# Replies 1

These are the decisions Copilot should follow for the open questions raised while reviewing:

- `docs/WEB_APP_CODE_REVIEW_FIX_SPEC.md`
- `docs/WEB_APP_CODE_REVIEW_FIX_TODO.md`

## 1. Should `job.json` remain publicly downloadable?

**No. The new review-doc rule wins.**

Keep only the canonical private metadata file here:

```text
data/jobs/<job_id>/job.json
```

Do **not** copy it to:

```text
data/jobs/<job_id>/artifacts/job.json
```

And do **not** allow this to succeed:

```text
GET /api/jobs/<job_id>/artifacts/job.json
```

Reason: `job.json` is server-side metadata. It may contain absolute paths, request internals, implementation details, or future private fields. Public artifacts should be intentionally curated files only.

Required public artifacts should be things like:

```text
project.zip
warnings.json
debug.json
```

If a downloadable job summary is wanted later, create a separate sanitized file, for example:

```text
artifacts/job_summary.json
```

with only public, relative, non-sensitive fields.

This resolves the conflict noted in the uploaded questions: the later review requirement overrides the earlier implementation behavior.

## 2. Should the Phase 7 regression-threshold changes be reverted?

**Revert them unless Copilot can prove the output-shape drift was caused by an intentional, reviewed engine/layout change.**

This web-app stabilization pass should not weaken core schematic-regression guardrails just to get a green test run. The task is to fix the web wrapper, not silently relax layout-quality thresholds.

Copilot should do this:

```text
1. Revert the threshold changes in tests/unit/test_phase7_regression_guardrails.py.
2. Run the relevant tests.
3. If they fail, investigate the actual cause.
4. If the failure is caused by intentional engine/layout changes, document that explicitly in the test file or a short docs note.
5. Only then keep adjusted thresholds.
```

Acceptable rationale:

```text
The layout/routing engine intentionally changed output geometry, and the new thresholds reflect the reviewed expected output.
```

Not acceptable rationale:

```text
The tests failed after the webapp work, so thresholds were loosened to make them pass.
```

Given the current information, **default action: revert the threshold loosenings.**

## 3. Should the new review docs be moved to the repo root?

**Yes. Move them to the repo root. Do not keep them only under `docs/`.**

They should exist here:

```text
WEB_APP_CODE_REVIEW_FIX_SPEC.md
WEB_APP_CODE_REVIEW_FIX_TODO.md
```

If Copilot wants to keep copies under `docs/`, that is optional, but the authoritative versions for this pass should be at repo root because that is what the TODO says and it matches the earlier migration-doc convention.

Preferred action:

```bash
git mv docs/WEB_APP_CODE_REVIEW_FIX_SPEC.md WEB_APP_CODE_REVIEW_FIX_SPEC.md
git mv docs/WEB_APP_CODE_REVIEW_FIX_TODO.md WEB_APP_CODE_REVIEW_FIX_TODO.md
```

Do not duplicate them unless there is a specific reason. Duplication creates drift.

## 4. How strong should the README web-app framing be?

**Use the stronger wording. The lighter framing is not enough.**

The README should explicitly say all three points:

```text
This branch provides a Python FastAPI web app for deterministic KiCad project generation from Circuit IR JSON.

The web app does not require OpenClaw or an LLM.

OpenClaw skill files are archived under legacy/openclaw-skill for reference.
```

This matters because the repo previously had OpenClaw/LLM-oriented framing. The webapp branch should make the new role unambiguous.

Recommended README intro:

```markdown
# KiCad PCB Web App

This branch provides a Python FastAPI web app for deterministic KiCad project generation from Circuit IR JSON.

The web app does not require OpenClaw, an LLM, an agent runtime, or any external AI service. Users provide explicit Circuit IR JSON, and the app validates it, generates KiCad project files, and exposes downloadable artifacts.

The previous OpenClaw skill files are archived under `legacy/openclaw-skill/` for reference only.
```

## Also confirm the no-decision items

Copilot's confirmed issue list is valid. It should fix all of these during the pass:

```text
validation default still "kicad"
empty symbol search returns 422 instead of 400
KiCadError / ToolError handling missing
job.json exposed as artifact
job detail UI still mostly raw JSON
Generate button lacks direct artifact links
package-data missing for bundled .kicad_sym files
stale kicad-pcb leftovers exist
review spec/TODO files are under docs instead of repo root
```

Those are real follow-up tasks, not new design questions.
