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
- [x] `12v-to-5v-3-3v-switching-regulator-module-aeonlabs-ai-volvo-mkii-open-hardware`
  - [x] Ingested
  - [x] Evaluated
  - [x] Triage complete
  - [x] Generic fix landed
  - [x] Re-evaluated
  - [x] Passes or has documented remaining blocker
- [x] `4-port-usb-20-hub-w-2-internal-ports-and-2-external-ports`
  - [x] Ingested
  - [x] Evaluated
  - [x] Triage complete
  - [x] Generic fix landed
  - [x] Re-evaluated
  - [x] Passes or has documented remaining blocker
- [x] `buck-converter-xl4015-incubadora`
  - [x] Ingested
  - [x] Evaluated
  - [x] Triage complete
  - [x] Generic fix landed
  - [x] Re-evaluated
  - [x] Passes or has documented remaining blocker
- [x] `lmr51450sdrrr-6v-36v-to-500v-4a-buck-converter`
  - [x] Ingested
  - [x] Evaluated
  - [x] Triage complete
  - [x] Generic fix landed
  - [x] Re-evaluated
  - [x] Passes or has documented remaining blocker
- [x] `p-channel-mosfet-load-switch-driver-aeonlabs-ai-volvo-mkii-open-hardware`
  - [x] Ingested
  - [x] Evaluated
  - [x] Triage complete
  - [x] Generic fix landed
  - [x] Re-evaluated
  - [x] Passes or has documented remaining blocker
- [x] `rp2040-microcontroller-core-circuit-mitayi-pico-d1`
  - [x] Ingested
  - [x] Evaluated
  - [x] Triage complete
  - [x] Generic fix landed
  - [x] Re-evaluated
  - [x] Passes or has documented remaining blocker
- [x] `solar-charger-mppt-circuit-sts1-pcb-sidepanel`
  - [x] Ingested
  - [x] Evaluated
  - [x] Triage complete
  - [x] Generic fix landed
  - [x] Re-evaluated
  - [x] Passes or has documented remaining blocker
- [x] `stm32g030-minimal-system-circuit-electrical`
  - [x] Ingested
  - [x] Evaluated
  - [x] Triage complete
  - [x] Generic fix landed
  - [x] Re-evaluated
  - [x] Passes or has documented remaining blocker
- [x] `w5500-spi-interface-decoupling-termination-openknx-reg1`
  - [x] Ingested
  - [x] Evaluated
  - [x] Triage complete
  - [x] Generic fix landed
  - [x] Re-evaluated
  - [x] Passes or has documented remaining blocker

Phase 3 notes:
- `code_review/generated/model_eval/summary.json` now reports `evaluated_count=18`, `failed_count=18`, `skipped_count=0`.
- Every new batch-2 fixture now has an evaluation report directory under `code_review/generated/model_eval/<fixture_id>/`.
- Current batch-2 split:
  - all 9 batch-2 fixtures now land as ordinary evaluation **failures** with `electrical_equivalence.status="failed"`; there are no remaining batch-2 runtime partials.
  - the recurring first mismatch is still net identity/connectivity drift (`net_names` plus net-membership mismatches such as `+12V`, `12MHZ_CLK`, `+5V`, `SW`, `CUR1_OUT`, `+3.3V_LOCAL`, and `Net-(C14-Pad1)` depending on the fixture).
  - no new batch-2 fixture is currently in the symbol/library/pin-lookup failure bucket after the retained embedded-symbol and pin-free-symbol harness fixes.

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
  - `rp2040-microcontroller-core-circuit-mitayi-pico-d1` -> baseline runtime issue was the evaluation/apply pipeline rejecting the genuine pin-free `Mechanical:MountingHole` symbol; fixed in Phase 5 by allowing resolved pin-free symbols to return an empty pin set.
  - `solar-charger-mppt-circuit-sts1-pcb-sidepanel` -> baseline runtime issue was Graphviz `dot` aborting with the deterministic `flat_reorder` assertion; fixed in Phase 5 by emitting feedback dummy-node constraints directly in the main DOT graph instead of a `cluster_feedback` subgraph.
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
- [x] Prioritize generic pipeline bugs that block multiple new fixtures from even being evaluated.
- [x] Add or update focused tests for every retained harness fix.
- [x] Re-run ingestion and evaluation after each retained harness fix.

Phase 5 notes:
- Retained harness fix: genuine pin-free symbols with a complete library definition chain now resolve as empty pin sets instead of raising `SYMBOL_HAS_NO_PINS`; broken `extends` chains still raise.
- Focused validation for that retained fix is green: `uv run pytest tests/unit/test_symbol_index.py tests/unit/test_circuit_ir.py`.
- `rp2040-microcontroller-core-circuit-mitayi-pico-d1` has been re-evaluated and now lands as a normal generator-quality **fail** (`electrical_equivalence`, `geometry_spread`, `relative_positions`, `label_strategy`, `overlap`, `role_counts`) instead of an `evaluation_runtime` partial.
- Retained Graphviz fix: feedback dummy-node constraints are now emitted directly in the main DOT graph instead of a `cluster_feedback` subgraph, which avoids the `flat_reorder` crash when block-zone anchor constraints are also active.
- Focused validation for that Graphviz fix is green: `uv run pytest tests/unit/test_phase4_layout.py -k feedback_dummy_nodes_have_empty_point_labels`.
- `solar-charger-mppt-circuit-sts1-pcb-sidepanel` has been re-evaluated and now lands as a normal generator-quality **fail** (`electrical_equivalence`, `geometry_spread`, `relative_positions`, `label_strategy`, `overlap`, `role_counts`) instead of an `evaluation_runtime` partial.
- Current runtime-partial batch-2 count is now 0 fixtures; all 9 batch-2 schematics now produce ordinary evaluation reports.
- A full ingest refresh after the retained harness fixes still reports 18 accepted / 0 partial / 0 rejected fixtures.

### 5.2 Fix recurring layout/routing/generator issues second
- [x] Prioritize generic generator fixes that improve more than one new fixture.
- [x] Add focused regression coverage for each retained generic fix.
- [x] Re-run the affected fixtures after each retained generator fix.

Phase 5.2 notes:
- Retained generator fix: promoted visible local/global labels now reuse `_label_attachment_plan(...)`, so promoted labels respect occupied wire/label points and protected stub points instead of always landing on the first candidate stub end.
- Focused validation for that retained fix is green: `uv run pytest tests/unit/test_phase6_wire_simplification.py -k "label_attachment_plan or promoted_visible_label"`.
- Retained generator fix: 4+-pin local bus nets can now take the protected shared-lane fallback instead of being limited to the mean spine when the current spine would cross already occupied route points.
- Focused validation for that retained fix is green: `uv run pytest tests/unit/test_phase6_wire_simplification.py -k "protected_shared_lane or colliding_spine"`.
- Retained generator fix: when a bus-style net would extend a pin stub into a foreign attachment point (foreign endpoint or foreign stub), the router now falls back to per-pin endpoint labels instead of writing a shorting wire path. Slash-scoped nets use global labels; ordinary local nets use local labels.
- Focused validation for that retained fix is green: `uv run pytest tests/unit/test_phase6_wire_simplification.py -k "foreign_attachment or multi_pin_stub_hits_foreign or two_pin_stub_hits_foreign"`.
- Retained generator fix: endpoint-label breakout labels now reuse `_label_attachment_plan(...)`, so endpoint-label fallback paths avoid already occupied label anchors instead of stacking multiple endpoint labels onto the same coordinate.
- Focused validation for that retained fix is green: `uv run pytest tests/unit/test_phase6_wire_simplification.py -k "foreign_attachment or pin_endpoint_label_breakout_avoids_occupied_prior_label_anchor"`.
- Retained generator fix: the aligned power-cluster router now compares its offset shared-lane candidates against the default centroid spine/hub route instead of always taking the least-bad aligned lane when every aligned lane still crosses protected points.
- Focused validation for that retained fix is green: `uv run pytest tests/unit/test_phase6_wire_simplification.py -k "compact_local_ground_cluster or compact_local_decoupling_cluster or aligned_two_pin_ground_cluster"`.
- Retained generator fix: endpoint-label foreign-attachment breakout now prefers perpendicular breakout anchors once it actually reaches `_append_pin_endpoint_labels(...)`, which removed the stale inline `/MPPT1_OUT` label anchors that were still sitting on later GND corridors.
- Retained generator fix: `_write_symbols(...)` now mirrors two-pin passives by 180 degrees when the mirrored pin order better matches their connected-net centroids and avoids foreign-net endpoint collisions; this restored the `R11` / `SC1_V+` side of the solar fixture without a fixture-specific override.
- Retained generator fix: multi-pin power clusters now abandon a shared cluster route when the selected GND/power topology would still cross a protected foreign attachment point, and instead emit direct per-pin power symbols for that cluster.
- Retained generator fix: direct per-pin power symbol attachments now reuse protected breakout planning instead of always reusing the default power stub, so later power nets do not short through occupied/shared foreign stub points in dense support bundles.
- Retained generator fix: when a slash-scoped power net has no matching library power symbol, `write_routing(...)` now places the fallback global label via protected anchor planning instead of a fixed orthogonal jog, which prevents the fallback wire/label pair from stamping onto nearby signal geometry.
- Retained generator fix: explicit router junctions are now split into real wire endpoints before simplification/writeout, so KiCad export keeps intended power-branch joins like the recovered `R64 pin 1` `+3.3V@SD` branch in the MicroSD fixture.
- Retained generator fix: multi-unit symbol emission now keeps the expanded placed refs internal for layout/routing but writes logical refs back into the schematic/bind-marker layer, and `SCH003` now allows duplicate logical refs only for distinct-unit placements of the same `lib_id`. This cleared the USB hub / STM32 split-ref runtime blocker and returned both fixtures to ordinary electrical-equivalence evaluation.
- Focused validation for the retained solar closeout fixes is green: `uv run pytest tests/unit/test_sch_apply.py tests/unit/test_phase6_wire_simplification.py -k "write_symbols_flips_two_pin_passive_to_match_pin_nets or ground_cluster_hits_foreign_endpoint or aligned_two_pin_ground_cluster or pin_endpoint_label_breakout or label_attachment_plan"`.
- Re-evaluating the representative fixtures after those retained fixes keeps all four fixtures in ordinary evaluation: `solar-charger-mppt-circuit-sts1-pcb-sidepanel`, `rp2040-microcontroller-core-circuit-mitayi-pico-d1`, `4-port-usb-20-hub-w-2-internal-ports-and-2-external-ports`, and `stm32g030-minimal-system-circuit-electrical`.
- The USB hub and STM32 fixtures no longer fail as split-ref/runtime partials. Their current remaining blockers are ordinary electrical-equivalence mismatches rooted in broader routing/connectivity drift (for example USB `12MHZ_CLK` / `D2-` over-connection and STM32 `+3.3V_LOCAL` / `3v3` collapse) plus the existing label/layout secondary buckets.
- The retained solar and MicroSD fixes are still kept because they addressed real generic routing/writeout problems, but the latest full 18-fixture corpus evaluation shows both fixtures currently regress back to ordinary `electrical_equivalence` failures (`CUR1_OUT` / `Net-(MPPT1-ICTRL_PLUS)` on solar; `+3.3V@SD` / `DET_A` on MicroSD).
- The current full 18-fixture corpus baseline is **18 failed**. The active blocker set is now uniformly electrical-equivalence drift across both the original and batch-2 fixtures rather than a smaller runtime-partial bucket.

### 5.3 Do not ship speculative hacks
- [x] Revert any attempted fix that worsens corpus results overall.
- [x] Keep only the best retained state after each experiment.
- [x] Document the remaining blocker when a failure surface is not yet solved.

---

## Phase 6 — Update documentation and status continuously

### 6.1 Keep this file current
- [x] Mark new batch-2 tasks and fixture checklists as work completes.
- [x] Add short blocker notes where needed instead of leaving silent partial progress.

### 6.2 Keep the original corpus TODO coherent
- [x] Update `docs/MODEL_KICAD_CORPUS_TODO.md` only when batch-2 work changes the overall corpus program status.
- [x] Do not mark the entire corpus workflow complete until both batch 1 and batch 2 are in an acceptable final state.

### 6.3 Keep memory and reports aligned
- [x] Update `memory.md` at meaningful milestones.
- [x] Preserve useful evaluation artifacts under `code_review/generated/model_eval/`.

---

## Phase 7 — Final validation and closeout for batch 2

### 7.1 Validation commands
- [x] Run `uv run ruff check .`.
- [x] Run `uv run mypy src/kicad_pcb src/kicad_pcb_web`.
- [x] Run `uv run pytest`.
- [x] Run `uv run pytest -m requires_kicad` if the retained changes affect KiCad-backed paths.
- [x] Run a fresh full corpus evaluation after the retained batch-2 fixes.

### 7.2 Batch-2 completion criteria
- [x] All newly added schematics are ingested and tracked.
- [x] Every new fixture has an evaluation result or an explicitly documented blocker.
- [x] Retained fixes are generic and regression-tested.
- [x] Existing batch-1 fixtures are not regressed silently.
- [x] Documentation reflects the real corpus state.

### 7.3 Optional final repository closeout
- [x] If the repo gate is green, commit the retained batch-2 corpus work.
- [x] Push the branch only after the retained validation state is understood and documented.
