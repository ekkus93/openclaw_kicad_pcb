# KiCad Web App Post-Hardening Closure Spec — 2026-08-09

## Purpose

This is a narrow closure and consistency pass following `KICAD_WEBAPP_POST_REVIEW_HARDENING_*`.

The previous hardening implementation is accepted. This pass does not reopen architecture or generation design; it closes documentation/configuration drift, makes completion state obvious, verifies repository hygiene, and records exact-SHA acceptance evidence for the current `webapp` branch.

## Starting point

- Branch: `webapp`
- Closure-loop starting SHA: `3d5879ae0eaab0d496cb5c4713ac234e7dc70312`
- Prior hardening implementation acceptance SHA: `bb77b4985c124dcf947b5f2da4ab3b4b486b2798`
- Prior hardening evidence SHA: `4ab942dc68e6922752ce283e49dcb343d231e715`
- README badge was just corrected to scope CI to `webapp`.

## Confirmed residual defect

The active README TOML example still includes removed settings:

```toml
[web]
default_host = "127.0.0.1"
default_port = 8000
```

The runtime schema accepts only `data_dir` and `mutation_lock_timeout_s` under `[web]`; unknown keys fail startup. Therefore the documented example is invalid and must be corrected.

## Scope

### 1. README/configuration correctness

Audit active user-facing configuration documentation against `src/kicad_pcb_web/settings.py`.

Requirements:

- remove stale `default_host` / `default_port` config examples;
- keep ASGI host/port documented as Uvicorn launch arguments, not application settings;
- verify active `[web]` and `[llm]` keys match the runtime schema;
- verify environment-variable names described by active docs match actual parsing;
- do not reintroduce compatibility shims for removed settings;
- unsupported behavior must remain rejected explicitly rather than accepted as a silent no-op.

### 2. CI badge correctness

The `webapp` README badge must remain branch-qualified:

```text
badge.svg?branch=webapp
```

and the click target must filter workflow runs to `webapp`.

### 3. Prior hardening TODO closure bookkeeping

The original TODO is a planning document containing mutually exclusive alternatives, conditional manual steps, and explicit `NOT PERFORMED` dispositions. It must not be mechanically converted to all `[x]`.

Add a prominent closure/status note that:

- identifies the hardening batch as completed;
- links to `KICAD_WEBAPP_POST_REVIEW_HARDENING_COMPLETION_2026-08-09.md`;
- explains that unchecked boxes remain because the document preserves planning history, mutually exclusive paths, and unperformed manual checks;
- points to the completion evidence as authoritative disposition.

### 4. Active documentation consistency

Audit active web-app documentation, including at minimum:

- `README.md`
- `docs/JOB_EXECUTION_MODEL.md`
- `docs/WIZARD_WORKFLOW_DESIGN.md` and/or current wizard design document
- `docs/WEBAPP_LOCAL_OPERATOR_GUIDE.md` and/or current operator guide
- example TOML/config files if present

Check for stale claims about:

- removed bind settings;
- removed network-probe setting;
- dynamic per-request settings reload;
- preview dependencies;
- direct synchronous generation HTTP semantics;
- wizard failure/retry and provenance behavior;
- debug artifact confidentiality.

Fix only confirmed drift.

### 5. Silent/no-op configuration audit

For every surviving setting in `WebSettings`/`LlmSettings`:

- confirm it has a runtime consumer or an explicit validation restriction;
- reject/remove settings that are accepted but operationally inert;
- preserve deliberate fail-closed restrictions such as unsupported streaming or disabled request-log redaction if those values are explicitly rejected at validation time;
- do not turn unsupported behavior into warnings/default fallbacks.

### 6. Repository hygiene

Verify final tree does not contain temporary one-shot CI helpers or generated failure/debug artifacts.

Permanent automation is allowed. Temporary helper workflows/scripts created solely for one-time edits must not remain.

### 7. Manual smoke disposition

Do not claim manual testing that was not executed.

The previous manual smoke items may remain `NOT PERFORMED` unless this closure loop actually executes them. Automated Playwright/API tests are not substitutes for human manual smoke evidence.

### 8. Exact-SHA acceptance

The final closure SHA must pass the permanent CI workflow on that exact commit.

Required permanent jobs:

1. Python lint, types, unit and web tests
2. Frontend lint, unit tests and production build
3. Build and install wheel/sdist
4. Browser smoke tests
5. KiCad integration tests

Record the final SHA, run ID/URL, job IDs and conclusions in a closure completion/evidence document.

## Explicit non-goals

Do not modify:

- schematic component placement;
- component orientation policy;
- wire routing;
- routing heuristics;
- crossing minimization;
- schematic spacing/layout algorithms;
- PCB placement/routing;
- unrelated Circuit IR semantics;
- unrelated UI design.

If a closure issue requires one of these areas, document the dependency instead of folding it into this batch.

## Failure semantics

- Documentation examples that the runtime rejects are defects and must be fixed.
- A configuration key must not be silently accepted if it has no effect.
- Unknown/removed configuration remains fail-closed.
- CI from an earlier SHA is not sufficient evidence for a later closure SHA.
- A red historical run does not invalidate a newer green exact-SHA run, but branch-scoped status presentation must be unambiguous.

## Definition of Done

Closure is complete when:

- README configuration examples match the live schema;
- `webapp` CI badge is branch-scoped and correctly linked;
- prior hardening TODO visibly points to authoritative completion evidence;
- active docs contain no confirmed hardening-era configuration/behavior drift;
- no accepted-but-inert configuration setting remains undispositioned;
- no temporary helper/generated failure artifacts remain in the final tree;
- manual smoke status is stated truthfully;
- no placement/routing redesign is included;
- permanent CI is 5/5 green on the exact final closure SHA;
- closure evidence records that exact result.
