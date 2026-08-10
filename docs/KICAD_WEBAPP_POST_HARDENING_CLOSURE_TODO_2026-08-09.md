# KiCad Web App Post-Hardening Closure TODO — 2026-08-09

Implementation checklist for:

```text
docs/KICAD_WEBAPP_POST_HARDENING_CLOSURE_SPEC_2026-08-09.md
```

Closure-loop starting SHA:

```text
3d5879ae0eaab0d496cb5c4713ac234e7dc70312
```

This is a closure/consistency batch. **Do not redesign schematic placement, orientation, wire routing, or PCB layout.**

---

# Phase 0 — Baseline and scope

- [ ] Confirm work is on `webapp`.
- [ ] Confirm closure spec and this TODO exist.
- [ ] Record exact starting SHA.
- [ ] Confirm prior hardening completion evidence exists.
- [ ] Confirm no placement/routing redesign is required for this batch.

---

# Phase 1 — Fix README configuration example

Inspect `README.md` and `src/kicad_pcb_web/settings.py`.

- [ ] Remove `[web].default_host` from the TOML example.
- [ ] Remove `[web].default_port` from the TOML example.
- [ ] Keep host/port documented as ASGI/Uvicorn launch arguments.
- [ ] Confirm every remaining `[web]` TOML key is accepted by runtime settings.
- [ ] Confirm every remaining `[llm]` TOML key is accepted by runtime settings.
- [ ] Confirm unsupported streaming is explicitly rejected when enabled rather than silently ignored.
- [ ] Confirm request-log redaction cannot be silently disabled.
- [ ] Confirm removed network-probe configuration remains rejected/removed.

Acceptance:

- [ ] A user copying the documented TOML example does not fail because of an unsupported key.

---

# Phase 2 — CI badge correctness

Inspect the first lines of `README.md`.

- [ ] Badge image URL includes `branch=webapp`.
- [ ] Badge click target filters CI workflow runs to `webapp`.
- [ ] Do not modify the `master` README as part of this closure unless separately required.

---

# Phase 3 — Prior hardening TODO closure banner

Update:

```text
docs/KICAD_WEBAPP_POST_REVIEW_HARDENING_TODO_2026-08-09.md
```

- [ ] Add a prominent completion/status note near the top.
- [ ] Link to `docs/KICAD_WEBAPP_POST_REVIEW_HARDENING_COMPLETION_2026-08-09.md`.
- [ ] Explain that unchecked boxes are preserved planning history, mutually exclusive alternatives, conditional/manual items, or explicitly dispositioned items.
- [ ] State that the completion evidence is authoritative for closure status.
- [ ] Do not mechanically convert all planning checkboxes to `[x]`.

---

# Phase 4 — Active documentation consistency audit

Inspect active web-app documentation and config examples.

At minimum:

```text
README.md
docs/JOB_EXECUTION_MODEL.md
docs/WIZARD_WORKFLOW_DESIGN.md
docs/WEBAPP_LOCAL_OPERATOR_GUIDE.md
```

Use the current file name if an older referenced wizard/operator document has been superseded.

Search for stale references to:

- [ ] `default_host`
- [ ] `default_port`
- [ ] `KICAD_PCB_WEB_HOST`
- [ ] `KICAD_PCB_WEB_PORT`
- [ ] `network_probe_enabled`
- [ ] `KICAD_PCB_WEB_LLM_NETWORK_PROBE_ENABLED`
- [ ] dynamic/per-request config reload claims
- [ ] incorrect preview dependency claims
- [ ] HTTP 2xx on failed synchronous generation
- [ ] stale-IR-as-current recovery semantics
- [ ] missing wizard provenance restrictions
- [ ] claims that request-log redaction also sanitizes raw debug artifact files

For each match:

- [ ] classify as active-correct, historical/reference-only, or stale-active-doc defect.
- [ ] fix only stale active documentation.
- [ ] do not rewrite archived historical review evidence solely to erase historical terminology.

---

# Phase 5 — Configuration consumer/no-op audit

Inspect:

```text
src/kicad_pcb_web/settings.py
src/kicad_pcb_web/services/llm/
src/kicad_pcb_web/main.py
src/kicad_pcb_web/deps.py
```

For all surviving `WebSettings` and `LlmSettings` fields:

- [ ] map field to runtime consumer or explicit validation-only restriction.
- [ ] `data_dir` has real filesystem effect.
- [ ] `mutation_lock_timeout_s` has real locking effect.
- [ ] provider/model/base URL/API key and request parameters have real LLM effects.
- [ ] structured-output repair bounds have real effects.
- [ ] retry settings have real transport effects.
- [ ] `debug_artifact_capture` has real effect and defaults off.
- [ ] `enable_streaming` is not accepted as a working feature; `true` must fail validation.
- [ ] `request_log_redaction=false` must fail validation; logging remains always redacted.
- [ ] no surviving accepted value is silently inert.

If a silent no-op is found:

- [ ] remove it or implement it deliberately.
- [ ] add/update tests.
- [ ] update docs.

---

# Phase 6 — Repository hygiene

Inspect final tree and workflow/script directories.

- [ ] `.github/workflows/` contains only permanent workflow files.
- [ ] no one-shot edit/helper workflow remains.
- [ ] no temporary helper script remains under `.github/scripts/` or equivalent.
- [ ] no Playwright failure screenshots/traces are committed.
- [ ] no local debug artifacts are committed.
- [ ] no generated package/test output is committed outside intended committed generated assets.

Do not delete legitimate permanent automation or curated fixtures.

---

# Phase 7 — Focused tests / static validation for closure edits

Because this batch should be documentation-only unless the audits find code defects:

- [ ] verify README/config snippets against runtime schema by inspection and existing settings tests.
- [ ] run/confirm workflow config and generated-tree guards through permanent CI.
- [ ] if code is changed, add focused regression tests for the discovered defect.
- [ ] do not weaken tests to make closure pass.

---

# Phase 8 — Manual smoke disposition

Previous hardening manual smoke remains distinct from automated evidence.

Record for this closure batch:

- Direct JSON manual smoke: `[ ] PASS  [ ] FAIL  [ ] NOT PERFORMED`
- Wizard happy-path manual smoke: `[ ] PASS  [ ] FAIL  [ ] NOT PERFORMED`
- Wizard failure/retry manual smoke: `[ ] PASS  [ ] FAIL  [ ] NOT PERFORMED`

- [ ] Never infer PASS from Playwright/API automation.
- [ ] If not performed, record `NOT PERFORMED` explicitly in closure evidence.

---

# Phase 9 — Exact-SHA permanent CI

Commit all closure changes and run permanent CI on the exact final implementation SHA.

Required jobs:

- [ ] Python lint, types, unit and web tests — PASS
- [ ] Frontend lint, unit tests and production build — PASS
- [ ] Build and install wheel/sdist — PASS
- [ ] Browser smoke tests — PASS
- [ ] KiCad integration tests — PASS

- [ ] Record run ID/URL.
- [ ] Record all five job IDs and conclusions.
- [ ] Do not use an earlier SHA's green run as final evidence.
- [ ] If CI fails, fix the exact failure and repeat.

---

# Phase 10 — Closure completion evidence

Create:

```text
docs/KICAD_WEBAPP_POST_HARDENING_CLOSURE_COMPLETION_2026-08-09.md
```

Record:

- [ ] closure starting SHA
- [ ] final implementation SHA
- [ ] exact final CI run ID/URL
- [ ] five permanent job IDs/results
- [ ] README configuration corrections
- [ ] badge disposition
- [ ] documentation consistency audit findings
- [ ] configuration no-op audit findings
- [ ] repository hygiene result
- [ ] manual smoke status
- [ ] confirmation that placement/routing code was untouched
- [ ] any residual issue intentionally deferred

If the completion document itself creates a successor documentation SHA, validate that exact successor SHA with permanent CI before final closure and distinguish implementation SHA from evidence SHA.

---

# Definition of Done

Do not declare this closure loop complete until:

- [ ] README config example matches the runtime schema.
- [ ] `webapp` badge is branch-scoped.
- [ ] prior hardening TODO visibly points to authoritative completion evidence.
- [ ] active docs contain no confirmed hardening-era behavior/config drift.
- [ ] no accepted-but-inert config value remains undispositioned.
- [ ] no temporary helper/generated failure artifacts remain.
- [ ] manual smoke is truthfully dispositioned.
- [ ] no schematic placement/orientation/wire-routing/PCB-layout redesign is included.
- [ ] permanent CI is 5/5 green on the exact final closure SHA.
- [ ] closure evidence records the exact accepted state.
