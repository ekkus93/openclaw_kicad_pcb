# KiCad schematic refinement HTTP/CLI adversarial production-boundary audit

Date: 2026-08-13

Starting `webapp` SHA: `88a429f32f3c5cc2e084a8350996130732130ab1`

## Scope

This audit covers the externally reachable bounded-refinement surfaces and the trusted state used to select a mutation target:

- `POST /api/refinement/run`;
- installed `kicad-refine` CLI;
- `RefinementRunRequest` validation;
- wizard-session persistence and identity binding;
- current wizard-to-job ownership binding;
- job workspace/schematic containment;
- refinement evidence/session reservation selection;
- provider/model/KiCad/resource-bound ownership;
- public error and job-detail disclosure.

The audit does not treat the lower-level Python service functions as an external API. Production HTTP and CLI both dispatch through `run_wizard_refinement_request()`.

## Findings and disposition

### A1 — refinement HTTP leaked trusted-resolver details through the global `WebServiceError` handler

**Status: fixed.**

The route previously sanitized `UserError` but allowed trusted-target `WebServiceError` subclasses to reach the generic application handler. Those errors can legitimately carry private orchestration details such as wizard/job identifiers.

The refinement route now handles both `UserError` and `WebServiceError` locally, preserves the controlled HTTP status/machine code, and emits only the fixed refinement failure message. Regression coverage uses canary session IDs, paths, and credentials and verifies that none are reflected.

### A2 — parent-like wizard session IDs could alias paths outside the intended session directory

**Status: fixed.**

The historical session-ID grammar allowed `.` and `..`. Shared wizard persistence joined the value directly below `wizard_sessions/`, so `..` could resolve outside the intended per-session namespace before the later refinement-root containment check ran.

The external refinement request and shared wizard-session storage boundary now reject parent-like syntax, including complete `.`/`..` segments and consecutive-dot forms. Generated wizard IDs are unaffected.

### A3 — wizard session directories and persisted IDs were not independently bound to the requested identity

**Status: fixed.**

Wizard-session storage now requires the canonical `wizard_sessions` root and each requested session directory to be non-symlink aliases. `read_wizard_session()` also verifies that the persisted `WizardSessionDetail.id` equals the requested session ID.

This prevents a filesystem alias or mismatched `wizard.json` from silently substituting another wizard session.

### A4 — identical Circuit IR was insufficient to prove that a current job belonged to the requesting wizard

**Status: fixed.**

The resolver already required wizard IR, job request IR, and canonical `input/circuit_ir.json` to agree. A tampered wizard could nevertheless reference another successful job with identical IR because jobs did not carry an independent wizard owner binding.

Wizard-created jobs now persist an internal wizard-session owner marker. Refinement requires an exact owner match before model dispatch or mutation. The marker is removed from public `JobDetail.request` serialization and remains available only in private persisted job state.

Jobs created before this ownership binding do not receive a permissive fallback. They fail closed for refinement and must be regenerated from the current wizard session.

### A5 — unexpected CLI failures could escape as traceback/exception text

**Status: fixed.**

Known CLI argument, configuration, `UserError`, and `WebServiceError` failures were already sanitized. Unexpected request/configuration exceptions could still escape through the Python CLI process, and a failing LLM client `close()` could replace the command result.

Unexpected request/configuration failures now return `REFINEMENT_CLI_INTERNAL_ERROR` with fixed text while logging only the exception type. Client cleanup failure is warning-visible, logs only the exception type, and does not replace the already-determined command result.

## Boundary matrix

- **Arbitrary filesystem target selection:** blocked. HTTP/CLI accept no schematic/work/evidence path. Trusted job schematic references are relative, canonicalized, `.kicad_sch`, and contained beneath the canonical job workspace. Wizard-session path aliases are now rejected earlier as well.
- **Stale or foreign project mutation:** blocked. The wizard must be completed, reference its current successful job, match that job by independent owner identity, and match both persisted job request IR and canonical input IR.
- **Provider/model/API-key selection:** blocked. They come only from validated process settings. External request/CLI override fields and flags are rejected before dispatch.
- **KiCad executable selection:** blocked. Production composition creates `KicadCliAdapter()` internally; external override attempts are rejected.
- **Resource/operation-policy override:** blocked. Loop bounds and policy come only from validated feature configuration. HTTP extras and CLI flags for bounds/policy are rejected before dispatch.
- **Reservation bypass:** blocked. External callers cannot supply a second refinement reservation/session ID. The validated wizard session ID is the only ID passed to the canonical refine service. Existing completed, active, stale, orphaned, and terminal-evidence-failure reservation tests remain fail-closed.
- **Secret/private-path reflection:** blocked at the refinement HTTP and CLI boundaries for malformed input, controlled service failures, trusted resolver failures, and unexpected CLI failures.
- **Alternate public mutation route:** none found. Production HTTP and CLI both terminate at `run_wizard_refinement_request()` before reaching configured/canonical refinement services.

## Local validation

The sandbox does not have Ruff or mypy installed, so this audit does not claim those repository-wide gates locally. No dependency or lint configuration was changed.

Local validation performed with the available Python environment:

- focused HTTP/wizard boundary and persistence matrix: `36 passed`;
- expanded boundary/configuration/session/job matrix: `76 passed, 2 skipped`;
- shared wizard generation/job regressions: `41 passed, 3 skipped`;
- web regression sweep excluding the stale local copy of the already-updated general CLI test: `327 passed, 6 skipped`;
- current CLI sanitization tests, including unexpected failures and forbidden override flags, passed;
- `python3 -m compileall -q src/kicad_pcb_web` passed;
- modified production/test modules pass `py_compile`.

Permanent CI status is intentionally not asserted in this audit document.

## Residual risk and next action

This audit intentionally makes pre-owner-binding wizard jobs ineligible for refinement rather than guessing ownership. Regenerating the current wizard project upgrades it to the bound job format.

The next project task is to reconcile the main refinement TODO/status checkboxes against implementation evidence. After reconciliation, continue with the experimental evaluation corpus and the broader Phase O unsafe-fallback/silent-failure audit.
