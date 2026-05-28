# TODO: Process New Model KiCad Corpus Batch 2

This TODO list is for GitHub Copilot. Implement the tasks in order. The goal is to ingest the newly added `.kicad_sch` files under `model_kicad_files/`, convert them into stable corpus fixtures, evaluate generated schematics against them, and use the resulting reports to improve the generic netlist-JSON-to-KiCad-schematic generator without regressing the first corpus batch.

Do not implement ML training. This is a deterministic corpus/evaluator/regression workflow.

Do not special-case individual fixture filenames or individual source schematic coordinates.

---

## Current problems

- The repository already has a working corpus ingest/evaluate pipeline, but the newly added raw schematics have not been processed through that pipeline yet.
- The first corpus batch still has at least one unresolved hard fixture (`microsd-card-in-spi-mode-with-hotswap-support`), so new work must not assume the corpus is already fully green.
- The full repo gate is green again (`uv run ruff check .`, `uv run mypy src/kicad_pcb src/kicad_pcb_web`, `uv run pytest`), so batch-2 work can now use a clean baseline while still distinguishing:
  1. pre-existing corpus fixture failures,
  2. new failures introduced by the second corpus batch,
  3. fixture-specific generator/evaluator issues.

## Proposed fixes

- Reuse the existing corpus ingestion/evaluation pipeline instead of inventing a new one.
- Track the new schematic files explicitly as a second processing batch.
- Ingest first, then evaluate, then triage by repeated generic failure patterns.
- Fix only generic ingestion, layout, routing, symbol, or evaluation issues; do not hard-code per-fixture exceptions.

---

## Phase 0 — Safety rules and repo orientation for batch 2

### 0.1 Reuse the existing corpus workflow
- [x] Re-read `docs/MODEL_KICAD_CORPUS_TODO.md` before changing the batch-2 workflow.
- [x] Re-read the current corpus commands in `src/kicad_pcb/commands/model_corpus.py`.
- [x] Re-read the current reporting flow in `src/kicad_pcb/evaluation/reports.py`.
- [x] Re-read the current fixture metadata/status handling in `src/kicad_pcb/corpus/metadata.py`.

### 0.2 Preserve existing behavior
- [x] Do not mutate raw files under `model_kicad_files/`.
- [x] Do not special-case individual new fixture filenames.
- [x] Do not weaken current validation or reporting just to accept difficult schematics.
- [x] Do not break already ingested batch-1 fixtures while processing batch 2.
- [x] Do not add new dependencies without explicit approval.

### 0.3 Use repo-standard validation commands
- [x] Use `uv run ruff check .` for linting.
- [x] Use `uv run mypy src/kicad_pcb src/kicad_pcb_web` for typing.
- [x] Use `uv run pytest` for the default suite.
- [x] Use `uv run pytest -m requires_kicad` only when KiCad-backed tests are required.

---

## Phase 1 — Inventory the new raw schematics

### 1.1 Confirm the new batch membership
- [x] Confirm these newly added source schematics are present in `model_kicad_files/`:
  - [x] `12v-to-5v-3-3v-switching-regulator-module-aeonlabs-ai-volvo-mkii-open-hardware.kicad_sch`
  - [x] `4-port-usb-20-hub-w-2-internal-ports-and-2-external-ports.kicad_sch`
  - [x] `buck-converter-xl4015-incubadora.kicad_sch`
  - [x] `lmr51450sdrrr-6v-36v-to-500v-4a-buck-converter.kicad_sch`
  - [x] `p-channel-mosfet-load-switch-driver-aeonlabs-ai-volvo-mkii-open-hardware.kicad_sch`
  - [x] `rp2040-microcontroller-core-circuit-mitayi-pico-d1.kicad_sch`
  - [x] `solar-charger-mppt-circuit-sts1-pcb-sidepanel.kicad_sch`
  - [x] `stm32g030-minimal-system-circuit-electrical.kicad_sch`
  - [x] `w5500-spi-interface-decoupling-termination-openknx-reg1.kicad_sch`

### 1.2 Record expected fixture ids
- [x] Compute and verify the deterministic fixture id for each new source file.
- [x] Confirm none of the new fixture ids collide with existing corpus fixture directories.
- [x] If a collision appears, resolve it only through the existing stable slug/hash logic.

### 1.3 Capture baseline status before ingesting
- [x] Record the current contents of `tests/fixtures/model_corpus/ingestion_report.json`.
- [x] Record the current fixture count under `tests/fixtures/model_corpus/`.
- [x] Record the current evaluation summary under `code_review/generated/model_eval/` if present.

Baseline notes:
- Pre-refresh `tests/fixtures/model_corpus/ingestion_report.json` shows `accepted_count=9`, `partial_count=0`, `rejected_count=0`.
- Pre-refresh fixture directory count under `tests/fixtures/model_corpus/` is `9`.
- Pre-refresh `code_review/generated/model_eval/summary.json` shows `evaluated_count=1`, `failed_count=1`, and only `microsd-card-in-spi-mode-with-hotswap-support` has been evaluated so far.

---

## Phase 2 — Ingest the new batch

### 2.1 Refresh corpus ingestion
- [x] Run:
  - [x] `uv run python -m kicad_pcb.cli model-corpus ingest --source-dir model_kicad_files --out-dir tests/fixtures/model_corpus --refresh --require-kicad`
- [x] Confirm the ingestion command completes without crashing.
- [x] Confirm `tests/fixtures/model_corpus/ingestion_report.json` is rewritten deterministically.

### 2.2 Verify fixture artifacts for each new schematic
- [x] For each newly added schematic, verify the refreshed fixture directory contains the expected artifacts when status is `ready`:
  - [x] `metadata.json`
  - [x] `source.kicad_sch`
  - [x] `source_normalized.kicad_sch`
  - [x] `source_layout_features.json`
  - [x] `source_netlist.kicadxml`
  - [x] `circuit_ir.json`
- [x] If a fixture is not `ready`, verify the metadata and ingestion report explain why.

### 2.3 Track ingest outcomes per new fixture
- [x] `12v-to-5v-3-3v-switching-regulator-module-aeonlabs-ai-volvo-mkii-open-hardware`
- [x] `4-port-usb-20-hub-w-2-internal-ports-and-2-external-ports`
- [x] `buck-converter-xl4015-incubadora`
- [x] `lmr51450sdrrr-6v-36v-to-500v-4a-buck-converter`
- [x] `p-channel-mosfet-load-switch-driver-aeonlabs-ai-volvo-mkii-open-hardware`
- [x] `rp2040-microcontroller-core-circuit-mitayi-pico-d1`
- [x] `solar-charger-mppt-circuit-sts1-pcb-sidepanel`
- [x] `stm32g030-minimal-system-circuit-electrical`
- [x] `w5500-spi-interface-decoupling-termination-openknx-reg1`

### 2.4 Batch-2 ingest acceptance criteria
- [x] Every new schematic is represented in `ingestion_report.json`.
- [x] Every new schematic is either `accepted`, `partial`, or `rejected` with explicit reasons.
- [x] No existing batch-1 fixture directories are lost or corrupted.

Phase 2 notes:
- The refreshed corpus now contains 18 fixture directories total (9 original + 9 new batch-2 fixtures).
- `tests/fixtures/model_corpus/ingestion_report.json` now reports `accepted_count=18`, `partial_count=0`, `rejected_count=0`.
- All 9 new batch-2 fixtures ingested as `ready` and contain `metadata.json`, `source.kicad_sch`, `source_normalized.kicad_sch`, `source_layout_features.json`, `source_netlist.kicadxml`, and `circuit_ir.json`.

---

## Phase 3 — Run baseline evaluation on the expanded corpus

### 3.1 Evaluate all ready fixtures after ingest refresh
- [x] Run:
  - [x] `uv run python -m kicad_pcb.cli model-corpus evaluate --corpus-dir tests/fixtures/model_corpus --out-dir code_review/generated/model_eval --require-kicad`
- [x] Confirm the command completes and writes refreshed summary artifacts.
- [x] Confirm every ready new fixture receives an evaluation report directory.

### 3.2 Capture batch-2 evaluation status
- [x] Record the new `code_review/generated/model_eval/summary.json`.
- [x] Record which of the new fixtures:
  - [x] pass electrical equivalence,
  - [x] fail electrical equivalence,
  - [x] fail layout/readability metrics,
  - [x] fail due to symbol/library/pin issues,
  - [x] fail due to evaluation harness issues.

### 3.3 Create a per-fixture triage checklist
- [ ] `12v-to-5v-3-3v-switching-regulator-module-aeonlabs-ai-volvo-mkii-open-hardware`
  - [x] Ingested
  - [x] Evaluated
  - [x] Triage complete
  - [ ] Generic fix landed
  - [ ] Re-evaluated
  - [ ] Passes or has documented remaining blocker
- [ ] `4-port-usb-20-hub-w-2-internal-ports-and-2-external-ports`
  - [x] Ingested
  - [x] Evaluated
  - [x] Triage complete
  - [ ] Generic fix landed
  - [ ] Re-evaluated
  - [ ] Passes or has documented remaining blocker
- [ ] `buck-converter-xl4015-incubadora`
  - [x] Ingested
  - [x] Evaluated
  - [x] Triage complete
  - [ ] Generic fix landed
  - [ ] Re-evaluated
  - [ ] Passes or has documented remaining blocker
- [ ] `lmr51450sdrrr-6v-36v-to-500v-4a-buck-converter`
  - [x] Ingested
  - [x] Evaluated
  - [x] Triage complete
  - [ ] Generic fix landed
  - [ ] Re-evaluated
  - [ ] Passes or has documented remaining blocker
- [ ] `p-channel-mosfet-load-switch-driver-aeonlabs-ai-volvo-mkii-open-hardware`
  - [x] Ingested
  - [x] Evaluated
  - [x] Triage complete
  - [ ] Generic fix landed
  - [ ] Re-evaluated
  - [ ] Passes or has documented remaining blocker
- [ ] `rp2040-microcontroller-core-circuit-mitayi-pico-d1`
  - [x] Ingested
  - [x] Evaluated
  - [x] Triage complete
  - [ ] Generic fix landed
  - [ ] Re-evaluated
  - [ ] Passes or has documented remaining blocker
- [ ] `solar-charger-mppt-circuit-sts1-pcb-sidepanel`
  - [x] Ingested
  - [x] Evaluated
  - [x] Triage complete
  - [ ] Generic fix landed
  - [ ] Re-evaluated
  - [ ] Passes or has documented remaining blocker
- [ ] `stm32g030-minimal-system-circuit-electrical`
  - [x] Ingested
  - [x] Evaluated
  - [x] Triage complete
  - [ ] Generic fix landed
  - [ ] Re-evaluated
  - [ ] Passes or has documented remaining blocker
- [ ] `w5500-spi-interface-decoupling-termination-openknx-reg1`
  - [x] Ingested
  - [x] Evaluated
  - [x] Triage complete
  - [ ] Generic fix landed
  - [ ] Re-evaluated
  - [ ] Passes or has documented remaining blocker

Phase 3 notes:
- `code_review/generated/model_eval/summary.json` now reports `evaluated_count=18`, `failed_count=18`, `skipped_count=0`.
- Every new batch-2 fixture now has an evaluation report directory under `code_review/generated/model_eval/<fixture_id>/`.
- Current batch-2 split:
  - 7 fixtures fail electrical equivalence and also show recurring layout/readability drift (`12v-to-5v-3-3v-switching-regulator-module-aeonlabs-ai-volvo-mkii-open-hardware`, `4-port-usb-20-hub-w-2-internal-ports-and-2-external-ports`, `buck-converter-xl4015-incubadora`, `lmr51450sdrrr-6v-36v-to-500v-4a-buck-converter`, `p-channel-mosfet-load-switch-driver-aeonlabs-ai-volvo-mkii-open-hardware`, `stm32g030-minimal-system-circuit-electrical`, `w5500-spi-interface-decoupling-termination-openknx-reg1`).
  - 2 fixtures currently land as evaluation-runtime partials with `electrical_equivalence.status="not_run"` (`rp2040-microcontroller-core-circuit-mitayi-pico-d1`, `solar-charger-mppt-circuit-sts1-pcb-sidepanel`).
  - No new batch-2 fixture is currently in the symbol/library/pin-lookup failure bucket after the alias-preserving embedded-symbol materialization fix.

---

## Phase 4 — Group failures by generic root cause

### 4.1 Triage ingestion and symbol issues
- [x] Identify any new fixtures blocked by:
  - [x] malformed source schematic structure — none isolated in the current batch-2 ready set.
  - [x] unsupported KiCad version quirks — none isolated after the KiCad 9 ingest/evaluate reruns.
  - [x] embedded/custom symbol extraction gaps — resolved in Phase 3 by the alias-preserving embedded-symbol materialization fix.
  - [x] symbol library lookup failures — none remain in the current batch-2 reports.
  - [x] CircuitIR validation/autofix failures — none in the current batch-2 ready set.

### 4.2 Triage evaluation/runtime issues
- [x] Identify any new fixtures blocked by:
  - [x] `kicad-cli` export failures — none remain after the Phase 3 embedded-symbol fix unblocked `4-channel-switched-constant-current-source`.
  - [x] Graphviz/dot layout failures — `solar-charger-mppt-circuit-sts1-pcb-sidepanel` now isolates a deterministic `dot` `flat_reorder` assertion failure.
  - [x] schematic emission crashes — `rp2040-microcontroller-core-circuit-mitayi-pico-d1` now isolates a pin-free `Mechanical:MountingHole` symbol resolution failure in the evaluation/apply pipeline.
  - [x] report generation failures — handled generically by `evaluation_runtime` partial reports; no separate writer-only crash bucket remains.
  - [x] nondeterministic artifact generation — not observed in the repeated Phase 3 reruns.

### 4.3 Triage generator-quality issues
- [x] Group recurring new-fixture failures into reusable buckets:
  - [x] power and ground topology — dominant in `12v-to-5v-3-3v-switching-regulator-module-aeonlabs-ai-volvo-mkii-open-hardware`, `buck-converter-xl4015-incubadora`, `lmr51450sdrrr-6v-36v-to-500v-4a-buck-converter`, `p-channel-mosfet-load-switch-driver-aeonlabs-ai-volvo-mkii-open-hardware`, and `w5500-spi-interface-decoupling-termination-openknx-reg1`.
  - [x] connector bundle routing — dominant in `4-port-usb-20-hub-w-2-internal-ports-and-2-external-ports` and also present in `w5500-spi-interface-decoupling-termination-openknx-reg1`.
  - [x] compact local decoupling placement — recurring in `buck-converter-xl4015-incubadora`, `lmr51450sdrrr-6v-36v-to-500v-4a-buck-converter`, and `w5500-spi-interface-decoupling-termination-openknx-reg1`.
  - [x] multi-pin bus/shared-lane routing — dominant in `4-port-usb-20-hub-w-2-internal-ports-and-2-external-ports` and `stm32g030-minimal-system-circuit-electrical`.
  - [x] visible label/global-label strategy — present across all 7 electrical-fail fixtures; excess global-label exposure is explicit on `4-port-usb-20-hub-w-2-internal-ports-and-2-external-ports` and `stm32g030-minimal-system-circuit-electrical`.
  - [x] multi-unit symbol orientation — not yet a dominant batch-2 failure bucket.
  - [x] layout spread / relative position drift — present across all 7 generator-quality failures, with `geometry_spread` also failing on 6 of the 7.
  - [x] netlist export identity mismatches — present across all 7 generator-quality failures via `electrical_equivalence`.

### 4.4 Triage acceptance criteria
- [x] Every failing new fixture is assigned a generic failure category.
- [x] No failure is tracked only as “fixture-specific weirdness” without a concrete technical description.

Phase 4 notes:
- Runtime/harness bucket:
  - `rp2040-microcontroller-core-circuit-mitayi-pico-d1` -> evaluation/apply pipeline cannot currently handle the pin-free `Mechanical:MountingHole` extends chain cleanly.
  - `solar-charger-mppt-circuit-sts1-pcb-sidepanel` -> Graphviz `dot` aborts with the deterministic `flat_reorder` assertion during layout.
- Generator-quality buckets:
  - `12v-to-5v-3-3v-switching-regulator-module-aeonlabs-ai-volvo-mkii-open-hardware` -> power/ground topology + layout spread / relative-position drift + netlist export identity mismatch.
  - `4-port-usb-20-hub-w-2-internal-ports-and-2-external-ports` -> connector bundle routing + multi-pin shared-lane routing + visible global-label strategy + layout spread drift.
  - `buck-converter-xl4015-incubadora` -> power/ground topology + compact local decoupling placement + layout spread drift.
  - `lmr51450sdrrr-6v-36v-to-500v-4a-buck-converter` -> power/ground topology + compact local decoupling placement + layout spread drift + stubby routing.
  - `p-channel-mosfet-load-switch-driver-aeonlabs-ai-volvo-mkii-open-hardware` -> power/ground topology + relative-position/layout spread drift.
  - `stm32g030-minimal-system-circuit-electrical` -> multi-pin shared-lane routing + visible global-label strategy + role-count / relative-position drift.
  - `w5500-spi-interface-decoupling-termination-openknx-reg1` -> connector/decoupling routing + power/ground topology + layout spread drift.

---

## Phase 5 — Implement generic fixes for batch-2 failures

### 5.1 Fix ingestion/harness issues first
- [ ] Prioritize generic pipeline bugs that block multiple new fixtures from even being evaluated.
- [ ] Add or update focused tests for every retained harness fix.
- [ ] Re-run ingestion and evaluation after each retained harness fix.

### 5.2 Fix recurring layout/routing/generator issues second
- [ ] Prioritize generic generator fixes that improve more than one new fixture.
- [ ] Add focused regression coverage for each retained generic fix.
- [ ] Re-run the affected fixtures after each retained generator fix.

### 5.3 Do not ship speculative hacks
- [ ] Revert any attempted fix that worsens corpus results overall.
- [ ] Keep only the best retained state after each experiment.
- [ ] Document the remaining blocker when a failure surface is not yet solved.

---

## Phase 6 — Update documentation and status continuously

### 6.1 Keep this file current
- [ ] Mark new batch-2 tasks and fixture checklists as work completes.
- [ ] Add short blocker notes where needed instead of leaving silent partial progress.

### 6.2 Keep the original corpus TODO coherent
- [ ] Update `docs/MODEL_KICAD_CORPUS_TODO.md` only when batch-2 work changes the overall corpus program status.
- [ ] Do not mark the entire corpus workflow complete until both batch 1 and batch 2 are in an acceptable final state.

### 6.3 Keep memory and reports aligned
- [ ] Update `memory.md` at meaningful milestones.
- [ ] Preserve useful evaluation artifacts under `code_review/generated/model_eval/`.

---

## Phase 7 — Final validation and closeout for batch 2

### 7.1 Validation commands
- [ ] Run `uv run ruff check .`.
- [ ] Run `uv run mypy src/kicad_pcb src/kicad_pcb_web`.
- [ ] Run `uv run pytest`.
- [ ] Run `uv run pytest -m requires_kicad` if the retained changes affect KiCad-backed paths.
- [ ] Run a fresh full corpus evaluation after the retained batch-2 fixes.

### 7.2 Batch-2 completion criteria
- [ ] All newly added schematics are ingested and tracked.
- [ ] Every new fixture has an evaluation result or an explicitly documented blocker.
- [ ] Retained fixes are generic and regression-tested.
- [ ] Existing batch-1 fixtures are not regressed silently.
- [ ] Documentation reflects the real corpus state.

### 7.3 Optional final repository closeout
- [ ] If the repo gate is green, commit the retained batch-2 corpus work.
- [ ] Push the branch only after the retained validation state is understood and documented.
