# KiCad Web App Post-Hardening Closure TODO — 2026-08-09

Implementation checklist for:

```text
docs/KICAD_WEBAPP_POST_HARDENING_CLOSURE_SPEC_2026-08-09.md
```

Closure-loop starting SHA:

```text
3d5879ae0eaab0d496cb5c4713ac234e7dc70312
```

Current implementation candidate is the commit containing this checklist update. Permanent exact-SHA CI remains the acceptance gate before Phase 9/10 and Definition of Done are closed.

This is a closure/consistency batch. **Do not redesign schematic placement, orientation, wire routing, or PCB layout.**

---

# Phase 0 — Baseline and scope

- [x] Confirm work is on `webapp`.
- [x] Confirm closure spec and this TODO exist.
- [x] Record exact starting SHA.
- [x] Confirm prior hardening completion evidence exists.
- [x] Confirm no placement/routing redesign is required for this batch.

---

# Phase 1 — Fix README configuration example

Inspect `README.md` and `src/kicad_pcb_web/settings.py`.

- [x] Remove `[web].default_host` from the TOML example.
- [x] Remove `[web].default_port` from the TOML example.
- [x] Keep host/port documented as ASGI/Uvicorn launch arguments.
- [x] Confirm every remaining `[web]` TOML key is accepted by runtime settings.
- [x] Confirm every remaining `[llm]` TOML key is accepted by runtime settings.
- [x] Confirm unsupported streaming is explicitly rejected when enabled rather than silently ignored.
- [x] Confirm request-log redaction cannot be silently disabled.
- [x] Confirm removed network-probe configuration remains rejected/removed.

Acceptance:

- [x] A user copying the documented TOML example does not fail because of an unsupported key.

---

# Phase 2 — CI badge correctness

Inspect the first lines of `README.md`.

- [x] Badge image URL includes `branch=webapp`.
- [x] Badge click target filters CI workflow runs to `webapp`.
- [x] Do not modify the `master` README as part of this closure unless separately required.

---

# Phase 3 — Prior hardening TODO closure banner

Update:

```text
docs/KICAD_WEBAPP_POST_REVIEW_HARDENING_TODO_2026-08-09.md
```

- [x] Add a prominent completion/status note near the top.
- [x] Link to `docs/KICAD_WEBAPP_POST_REVIEW_HARDENING_COMPLETION_2026-08-09.md`.
- [x] Explain that unchecked boxes are preserved planning history, mutually exclusive alternatives, conditional/manual items, or explicitly dispositioned items.
- [x] State that the completion evidence is authoritative for closure status.
- [x] Do not mechanically convert all planning checkboxes to `[x]`.

---

# Phase 4 — Active documentation consistency audit

Inspected active web-app documentation and config examples:

```text
README.md
docs/JOB_EXECUTION_MODEL.md
docs/LLM_WIZARD_DESIGN.md
docs/LLM_WIZARD_OPERATOR_GUIDE.md
```

The older planning names `WIZARD_WORKFLOW_DESIGN.md` / `WEBAPP_LOCAL_OPERATOR_GUIDE.md` are not active files; the `LLM_WIZARD_*` documents above are the current equivalents.

Search/audit dispositions:

- [x] `default_host` — stale active README example fixed; historical planning references left intact.
- [x] `default_port` — stale active README example fixed; historical planning references left intact.
- [x] `KICAD_PCB_WEB_HOST` — removed runtime env remains fail-closed; no active doc advertises it as supported.
- [x] `KICAD_PCB_WEB_PORT` — removed runtime env remains fail-closed; no active doc advertises it as supported.
- [x] `network_probe_enabled` — active docs correctly say it was removed.
- [x] `KICAD_PCB_WEB_LLM_NETWORK_PROBE_ENABLED` — runtime rejects it; no active doc advertises it.
- [x] dynamic/per-request config reload claims — active docs correctly describe immutable startup snapshot/restart semantics.
- [x] incorrect preview dependency claims — active docs correctly require both `kicad-cli` and `rsvg-convert`.
- [x] HTTP 2xx on failed synchronous generation — active docs correctly document non-2xx failure semantics.
- [x] stale-IR-as-current recovery semantics — active docs explicitly prohibit stale preserved IR from becoming actionable.
- [x] missing wizard provenance restrictions — active docs describe provider/model/prompt/endpoint provenance conflicts.
- [x] claims that request-log redaction also sanitizes raw debug artifact files — active docs correctly state that it does not.

For each match:

- [x] classify as active-correct, historical/reference-only, or stale-active-doc defect.
- [x] fix only stale active documentation.
- [x] do not rewrite archived historical review evidence solely to erase historical terminology.

---

# Phase 5 — Configuration consumer/no-op audit

Inspected `src/kicad_pcb_web/settings.py`, `src/kicad_pcb_web/services/llm/`, wizard orchestration/session I/O, and resource locking.

For all surviving `WebSettings` and `LlmSettings` fields:

- [x] map field to runtime consumer or explicit validation-only restriction.
- [x] `data_dir` has real filesystem effect (jobs, wizard sessions, locks).
- [x] `mutation_lock_timeout_s` has real locking effect through `resource_lock()`.
- [x] provider/model/base URL/API key and request parameters have real LLM effects.
- [x] structured-output repair bounds have real effects in wizard JSON repair loops.
- [x] retry settings have real transport effects in `BaseHttpLlmClient`.
- [x] `debug_artifact_capture` has real effect and defaults off.
- [x] `enable_streaming` is not accepted as a working feature; `true` fails validation.
- [x] `request_log_redaction=false` fails validation; logging remains always redacted.
- [x] no surviving accepted value is silently inert.

Conditional remediation if a silent no-op were found:

- [x] No silent accepted no-op was found; no removal/implementation change required.
- [x] No new code regression test required because no product-code defect was found.
- [x] Active docs already reflect the validation-only restrictions.

---

# Phase 6 — Repository hygiene

Inspected final tree and workflow/script directories.

- [x] `.github/workflows/` contains only permanent `ci.yml` after the bounded helper self-deleted.
- [x] no one-shot edit/helper workflow remains.
- [x] no temporary helper script remains under `.github/scripts/` or equivalent.
- [x] no committed `frontend/test-results` directory / Playwright failure screenshots or traces.
- [x] no committed `frontend/playwright-report` directory.
- [x] no local wizard debug artifacts were added by this documentation-only batch.
- [x] no generated package/test output was added outside intended committed generated assets.

Do not delete legitimate permanent automation or curated fixtures.

---

# Phase 7 — Focused tests / static validation for closure edits

Because this batch is documentation-only:

- [x] verify README/config snippets against runtime schema by inspection and existing settings validation.
- [ ] confirm workflow config and generated-tree guards through permanent CI (Phase 9).
- [x] no product code was changed, so no new focused regression test is required.
- [x] no tests were weakened.

---

# Phase 8 — Manual smoke disposition

Previous hardening manual smoke remains distinct from automated evidence.

Record for this closure batch:

- Direct JSON manual smoke: `[ ] PASS  [ ] FAIL  [x] NOT PERFORMED`
- Wizard happy-path manual smoke: `[ ] PASS  [ ] FAIL  [x] NOT PERFORMED`
- Wizard failure/retry manual smoke: `[ ] PASS  [ ] FAIL  [x] NOT PERFORMED`

- [x] Never infer PASS from Playwright/API automation.
- [x] Record `NOT PERFORMED` explicitly in closure evidence.

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
- [x] Do not use an earlier SHA's green run as final evidence.
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

- [x] README config example matches the runtime schema.
- [x] `webapp` badge is branch-scoped.
- [x] prior hardening TODO visibly points to authoritative completion evidence.
- [x] active docs contain no confirmed hardening-era behavior/config drift.
- [x] no accepted-but-inert config value remains undispositioned.
- [x] no temporary helper/generated failure artifacts remain.
- [x] manual smoke is truthfully dispositioned.
- [x] no schematic placement/orientation/wire-routing/PCB-layout redesign is included.
- [ ] permanent CI is 5/5 green on the exact final closure SHA.
- [ ] closure evidence records the exact accepted state.
