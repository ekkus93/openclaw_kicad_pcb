# CODE_REVIEW8_TODO

## Goal

Implement the fixes required by the CODE_REVIEW8 findings so that the OpenClaw KiCad PCB skill can:

- reject invalid generated circuits before writing artifacts
- produce structurally valid KiCad schematics
- correctly generate the reviewed NE5532 and 555 example circuits
- improve schematic readability and domain correctness
- restore confidence in the test suite for future Copilot-driven refactors

---

## Phase 0 — Stabilize the branch and capture the current failure state

### Task 0.1 — Create a focused remediation branch
- [x] Create a dedicated branch for CODE_REVIEW8 remediation.
- [x] Record the current failing tests and known broken examples.
- [x] Preserve the current generated outputs for the NE5532 and 555 examples as regression inputs.

### Task 0.2 — Capture baseline evidence
- [x] Re-run the full test suite and save the output.
- [x] Re-run the targeted unit tests around block detection and symbol fixtures.
- [x] Run the current end-to-end generation flow for:
  - [x] NE5532 headphone amp
  - [x] 555 PWM LED dimmer
- [x] Save all generated artifacts and logs to a reproducible regression directory.

### Task 0.3 — Inventory the generation pipeline
- [x] Identify the exact code path(s) used to generate the NE5532 example.
- [x] Identify the exact code path(s) used to generate the 555 example.
- [x] Confirm whether the 555 example goes through the same validated IR path as the amp example.
- [x] Document any legacy, fallback, or side-path exporters still reachable from CLI or packaging logic.

Phase 0 notes:
- Regression evidence is captured under `code_review/generated/code_review8_phase0/`.
- The supported NE5532 CLI path is `new-from-netlist` with the readability fixture IR plus `--symbols-dir tests/fixtures/symbols`; it succeeds through `cmd_new_from_netlist -> _apply_netlist_to_project -> mutate_and_validate_sch`.
- The reviewed 555 input is the external `/home/ubo/.openclaw/workspace/555_PWM_LED_Dimmer.net` artifact. The public CLI rejects it as non-schema Circuit IR, and even the deterministic auto-fix path still fails schema validation after stripping legacy fields.
- The 555 artifact therefore does not currently travel through the same validated IR path as the NE5532 fixture; its active path is effectively an unsupported side format entering the public CLI and failing before generation.

---

## Phase 1 — Enforce hard output-validation gates

### Task 1.1 — Define the mandatory output contract
- [x] Write down the minimum invariants that every generated schematic must satisfy.
- [x] Convert these invariants into code-level post-generation validation checks.

#### Subtasks
- [x] Require that a generated schematic can be reparsed by the project’s own document model.
- [x] Require symbol count > 0 for non-empty generated designs.
- [x] Require wire count > 0 when the design is expected to contain wires.
- [x] Require that all referenced components exist in the emitted schematic.
- [x] Require that all declared nets are internally consistent.
- [x] Require that every generated artifact carries enough structure to be considered a real KiCad schematic.

### Task 1.2 — Add a post-generation reparse validator
- [x] Implement a step that reopens every generated `.kicad_sch` immediately after emission.
- [x] Validate the reparsed document before packaging or final write succeeds.
- [x] Fail generation if reparsing reveals empty or pseudo-populated schematics.

#### Subtasks
- [x] Create a reusable `validate_generated_schematic(...)` helper.
- [x] Return structured diagnostics suitable for CLI, tests, and CI.
- [x] Include counts for symbols, wires, labels, junctions, and unresolved references.
- [x] Add a “hard fail” severity for structural invalidity.

### Task 1.3 — Block artifact writes when hard validation fails
- [x] Ensure no `.kicad_sch`, project package, or managed-sheet update is finalized if post-generation validation fails.
- [x] Ensure partial outputs are not silently left behind in a misleading “successful” state.
- [x] Make CLI / skill results clearly report failure reason and location.

Phase 1 notes:
- Minimum hard invariants are now enforced in `validate_generated_schematic(...)`: internal reparse must succeed; non-empty designs must emit enough placed symbols; routed designs that expected wires must still contain wires after reparse; every generated component reference must exist; every expected pin-to-net binding must survive reparse without missing, unexpected, or duplicated bindings.
- Structured diagnostics are attached to `ApplyNetlistResult` and `NewFromNetlistResult`, included in the warning sidecar JSON, and summarized in CLI formatting.
- Structural invalidity now raises before success returns. When the managed sheet was only a newly created stub, the existing cleanup path removes it so failed runs do not leave a misleading `OpenClaw_Managed.kicad_sch` behind.

---

## Phase 2 — Fix net construction and net uniqueness guarantees

### Task 2.1 — Add canonical pin-to-net uniqueness enforcement
- [x] Add a central validation rule: each `(ref, pin)` may belong to only one canonical net.
- [x] Run this check before schematic emission.
- [x] Run this check again after emission / reparse when possible.

#### Subtasks
- [x] Build a reusable pin-membership index from the IR/net model.
- [x] Detect duplicated `(ref, pin)` assignments.
- [x] Emit diagnostics that name:
  - [x] component reference
  - [x] pin number
  - [x] conflicting nets
  - [x] source of each conflicting assignment if traceable
- [x] Add unit tests for duplicated pin membership.

### Task 2.2 — Audit net merge / alias logic
- [x] Find all places where nets are merged, aliased, normalized, or rewritten.
- [x] Confirm there is exactly one canonical representation for electrically identical nets.
- [x] Remove or fix any code that can duplicate pin membership across renamed/merged nets.

#### Subtasks
- [x] Audit IR net normalization.
- [x] Audit schematic serializer net handling.
- [x] Audit import/export adapters for alternate net formats.
- [x] Audit any “smart merge” logic used during managed-sheet application.

### Task 2.3 — Add hard tests for invalid-net rejection
- [x] Add unit tests that intentionally construct invalid circuits with duplicated pin/net assignments.
- [x] Assert that generation fails before write.
- [x] Add integration tests that verify invalid artifacts cannot be packaged as success.

Phase 2 notes:
- Canonical pin membership is now centralized in `build_pin_membership_index(...)` / `find_pin_membership_collisions(...)` inside `ir.validate`; `validate_circuit_ir(...)` uses that reusable index before any schematic emission.
- Collision diagnostics now include the component reference, pin number, distinct conflicting nets, and per-assignment provenance (`net_index`, `pin_index`, optional `unit`) so the source of each duplicate assignment is traceable.
- The post-emission/reparse side remains covered by the Phase 1 hard validator: duplicate emitted pin-to-net bindings still fail `validate_generated_schematic(...)` before success returns.
- Audit result: the supported generation pipeline has one canonical IR net representation. The only net alias normalization found on that path is ground-name canonicalization via `normalize_gnd_net_name(...)` during Circuit IR ingestion and schematic preflight; no separate smart-merge or alternate-format rewrite path was found in managed-sheet application.
- Regression coverage now includes direct IR-level collision diagnostics plus command-path rejection tests proving `apply-netlist` fails before managed-sheet write and `new-from-netlist` fails before project creation.

---

## Phase 3 — Identify and eliminate legacy / bypass export paths

### Task 3.1 — Find all generation entry points
- [x] Enumerate all CLI commands and internal APIs that can produce schematics.
- [x] Determine which ones use the validated IR path.
- [x] Determine which ones bypass it.

### Task 3.2 — Remove or quarantine unsafe paths
- [x] Deprecate any legacy path that does not enforce the full validation chain.
- [x] Route all supported generation through a single validated pipeline.
- [x] If removal is risky, gate old paths behind an explicit dev-only flag with loud warnings.

### Task 3.3 — Add tracing/logging for pipeline stages
- [x] Add structured debug logging so it is obvious which pipeline stages ran for a given generation request.
- [x] Include markers for:
  - [x] IR creation
  - [x] semantic validation
  - [x] schematic emission
  - [x] post-generation reparse
  - [x] packaging/write completion

Phase 3 notes:
- Verified entry-point inventory: the only supported IR-driven schematic-generation surfaces are `cmd_apply_netlist(...)` and `cmd_new_from_netlist(...)`, both of which converge on `_apply_netlist_to_project(...)` and therefore inherit schema, semantic, symbol/pin, managed-sheet, and post-generation reparse validation.
- `cmd_new(...)` plus `minimal_schematic_text()` only scaffold thin root project files; `commands/sch.py` and `commands/patterns.py` mutate existing schematics through `mutate_and_validate_sch(...)` but do not bypass the supported netlist-generation pipeline.
- Phase 0's unsupported 555 side-format remains outside the supported generation path rather than an active validated exporter bypass, so no additional runtime quarantine flag was required in the public CLI surface.
- The schematic debug dump now records `validated_pipeline_path` and ordered `pipeline_stage_markers` for `ir_creation`, `semantic_validation`, `schematic_emission`, `post_generation_reparse`, and `artifact_finalize`, making it explicit which validated stages ran for a generation request.

---

## Phase 4 — Repair the 555 PWM LED dimmer generation path

### Task 4.1 — Create a precise reference topology for the 555 PWM dimmer
- [x] Write a canonical internal reference representation for the intended 555 PWM circuit.
- [x] Make the intended topology explicit in tests and docs.

#### Required electrical expectations
- [x] Pin 1 -> GND
- [x] Pin 8 -> +12V
- [x] Pin 4 (RESET) -> +12V
- [x] Pin 5 (CTRL) -> small capacitor to GND only
- [x] Pins 2 and 6 tied together as a single timing node
- [x] Timing capacitor from timing node to GND
- [x] Proper charge/discharge steering network using two diodes and pot
- [x] Pin 3 -> gate resistor -> MOSFET gate
- [x] Gate pull-down from gate to GND only
- [x] MOSFET source -> GND
- [x] MOSFET drain -> LED negative
- [x] LED positive -> +12V

### Task 4.2 — Audit the 555 example input format
- [x] Inspect the uploaded / generated 555 net description and compare it to the repo’s real IR schema.
- [ ] Determine whether the 555 example is being produced from:
  - [ ] an outdated schema
  - [ ] a legacy exporter
  - [x] a custom side format
  - [ ] a malformed translation layer
- [x] Either migrate that input to the real IR schema or delete the unsupported path.

### Task 4.3 — Fix timing-node generation
- [x] Ensure pins 2 and 6 are represented as one and only one timing node.
- [x] Ensure the timing capacitor is placed from that node to ground.
- [x] Add tests that fail if the capacitor is placed across supply rails instead.

### Task 4.4 — Fix control-pin capacitor generation
- [x] Ensure pin 5 capacitor only connects from CTRL to GND.
- [x] Add a domain lint rule that flags any CTRL capacitor connected to VCC or any non-ground node.

### Task 4.5 — Fix diode steering network generation
- [x] Rebuild the charge/discharge steering logic explicitly instead of relying on loose generic net assembly.
- [x] Verify the direction of both diodes in the chosen topology.
- [x] Add topology-level tests for charge and discharge path placement.

### Task 4.6 — Fix MOSFET gate path generation
- [x] Ensure the 555 output goes through a dedicated gate resistor to the MOSFET gate.
- [x] Ensure the gate pull-down resistor goes only from gate to ground.
- [x] Add a lint rule that fails if the gate pull-down touches the timing node or any unrelated oscillator node.

### Task 4.7 — Fix LED load topology generation
- [x] Make the load path explicit and readable:
  - [x] +12V -> LED load connector positive
  - [x] LED load connector negative -> MOSFET drain
  - [x] MOSFET source -> GND
- [x] Verify connector pin mapping is consistent and readable.

### Task 4.8 — Fix timing value selection
- [x] Review how component values are chosen for the target PWM frequency.
- [x] Ensure the selected timing capacitor and resistances produce the intended frequency range.
- [x] Add a simple frequency-estimation validator or advisory.
- [x] Add a test that rejects obviously out-of-range value sets.

### Task 4.9 — Fix 555 schematic readability
- [x] Place the 555 centrally or center-left.
- [x] Group timing components around the 555.
- [x] Place the MOSFET and LED load block to the right.
- [x] Put power input and bulk decoupling at top-left or left.
- [x] Keep the gate path short and visually obvious.
- [x] Ensure the oscillator, control, and power-switch blocks are visually distinct.

### Task 4.10 — Add end-to-end golden tests for the 555 circuit
- [x] Add a test fixture for the intended 555 PWM design.
- [x] Assert structural validity of the generated schematic.
- [x] Assert no duplicated pin/net assignments.
- [x] Assert the expected topology is present.
- [x] Assert the emitted schematic is not empty / pseudo-populated.
- [x] Add snapshot or semantic golden tests for readability-critical placement.

Interim Phase 4 notes:
- Added deterministic auto-fix conversion from the reviewed legacy 555 `designName` / `connections[]` side format into canonical Circuit IR so the supported CLI path can validate and generate it.
- Added a canonical in-test 555 PWM reference topology plus advisory checks for mandatory pin roles, timing-node wiring, CTRL-cap targeting, steering-network shape, low-side load wiring, and frequency-range sanity.
- Added a checked-in canonical 555 PWM fixture plus regression tests that validate the fixture IR, require zero 555-specific advisories on the canonical design, and confirm the generated schematic is structurally populated with the expected refs.
- Tightened connector-role inference so load connectors like `LED_LOAD` are treated as output sinks, which moved the MOSFET/load block to the right and made the canonical 555 layout stable enough for semantic placement assertions.
- Added canonical-fixture regressions that lock in the intended 555 steering-diode direction and fail when the timing capacitor is moved onto the supply rail.
- Added a canonical-fixture regression that moves the gate pull-down onto the timing node and requires the 555 oscillator-net warning to fire.
- Reviewed the current Phase 4 value-selection path: the canonical 555 fixture and legacy-side-format conversion preserve source component values rather than synthesizing new ones, and the frequency-range advisory now serves as the guardrail on those preserved values.
- The legacy 555 repair path now rebuilds the timing, steering, gate-drive, and load nets explicitly, including connector-ref normalization for load/output handling, instead of preserving the old broken side-format wiring.

---

## Phase 5 — Repair the NE5532 headphone amplifier generation path

### Task 5.1 — Fix the input coupling capacitor topology
- [x] Identify where the input coupling capacitor and input resistor are assigned.
- [x] Prevent the coupling capacitor from being bypassed by a resistor across the same two nodes.

#### Subtasks
- [x] Decide the intended role of the input resistor:
  - [x] remove it entirely
  - [ ] or move it from post-cap node to ground as the input impedance / bias path
- [x] Add an audio lint rule that flags any coupling capacitor directly paralleled by a resistor unless explicitly allowed.

### Task 5.2 — Fix ambiguous connector handling
- [x] Decide whether the design should be mono or stereo.
- [x] If mono:
  - [x] keep the authored TRS symbols and explicitly no-connect the unused ring pins
- [ ] If stereo:
  - [ ] duplicate the second channel properly
  - [ ] add tests verifying both channels are present and symmetric

### Task 5.3 — Review output-stage semantics
- [x] Decide whether the circuit is intended as:
  - [ ] preamp / line driver
  - [x] light headphone driver
  - [ ] true headphone amplifier
- [x] Align generated documentation / warnings / design choices accordingly.

#### Subtasks
- [x] Review the default value and rationale for the output resistor.
- [x] Add an advisory when output impedance is high relative to likely headphone loads.
- [x] Add an advisory when the selected op-amp is being presented as a speaker-power solution.

### Task 5.4 — Review inter-stage coupling on split rails
- [x] Decide whether inter-stage AC coupling is actually required in the default design.
- [ ] If not required, simplify the topology.
- [x] If kept intentionally, document the design trade-off and make the rule explicit.

### Task 5.5 — Improve NE5532 schematic readability
- [ ] Separate the schematic into clear blocks:
  - [ ] input / connector / coupling
  - [ ] volume control
  - [ ] gain stage
  - [ ] buffer / follower
  - [ ] output protection / coupling
  - [ ] power and decoupling
- [ ] Move decoupling to a clean supply cluster near the power unit.
- [ ] Reduce vertical crowding around the op-amp core.
- [ ] Improve connector placement and spacing.

### Task 5.6 — Add end-to-end golden tests for the NE5532 example
- [x] Add a regression fixture for the reviewed amplifier.
- [x] Assert:
  - [x] structurally valid schematic
  - [x] no duplicated pin/net assignments
  - [x] no bypassed input coupling capacitor
  - [x] explicit handling of unused connector pins
  - [x] acceptable layout/readability metrics

Phase 5 notes:
- The reviewed NE5532 source netlist in `code_review/ne5532_headphone_amp_netlist.json` now removes the old `R1` bypass path so `C5` is the sole element between `LEFT_IN` and `IN_L_AC`.
- Advisory warnings now explicitly encode the settled mono-left policy (`Connector:AudioJack3` plus generated `no_connect` markers), the light-headphone-driver interpretation (`HEADPHONE_OUTPUT_IMPEDANCE_HIGH`), the speaker-misuse warning (`OPAMP_PRESENTED_AS_SPEAKER_POWER_STAGE`), and the intentional split-rail inter-stage coupling trade-off (`SPLIT_RAIL_INTERSTAGE_AC_COUPLING_PRESENT`).
- The named readability fixture `tests/fixtures/readability/ne5532_headphone_amp_left_current/` now mirrors the real reviewed NE5532 circuit instead of the retired passive placeholder IR, and the phase/readability tests were updated to use the real fixture semantics.

---

## Phase 6 — Strengthen domain-specific linting

### Task 6.1 — Create circuit-family lint rule infrastructure if missing
- [x] Add a clean way to register lint rules by circuit family or block type.
- [x] Support both warnings and hard-fail severities.

### Task 6.2 — Add audio/op-amp lint rules
- [x] Coupling capacitor bypass detection.
- [x] Feedback loop sanity checks.
- [x] Explicit unused connector-pin requirement.
- [x] Advisory for high output impedance in headphone-oriented designs.
- [x] Advisory for incomplete stereo implementation when TRS connectors are used.

### Task 6.3 — Add 555 PWM dimmer lint rules
- [x] Pin 1 must be GND.
- [x] Pin 8 must be VCC.
- [x] Pin 4 must be tied high.
- [x] Pins 2 and 6 must be a single timing node.
- [x] Timing capacitor must connect timing node to GND.
- [x] CTRL capacitor must connect only to GND.
- [x] Gate pull-down must connect only gate to GND.
- [x] MOSFET source must be GND in the intended low-side topology.
- [x] LED/load topology must match low-side switching expectations.
- [x] Frequency-value sanity advisory or hard threshold rule.

### Task 6.4 — Promote selected advisories to hard failures
- [x] Duplicate pin-to-net assignments -> hard fail
- [x] Empty / pseudo-populated schematic -> hard fail
- [x] Contradictory mandatory 555 pin roles -> hard fail
- [ ] Bypassed input coupling capacitor in audio stage -> at least warning, possibly hard fail for generated reference designs
- [ ] Ambiguous unused connector pins -> warning or hard fail based on policy

Phase 6 notes:
- Domain-specific linting now runs through a small circuit-family registry in `commands/_validate.py` instead of one flat warning accumulator. The registry currently separates `generic`, `audio_connector`, `audio_opamp`, and `timer555_pwm` rules.
- Advisory payloads now carry both `family` and `severity`, while preserving the existing JSON warning shape for callers and tests.
- Mandatory 555 topology violations (`TIMER555_*` role/timing/gate/load structure faults except the frequency advisory) are now tagged `hard_fail` and rejected by `validate-netlist`, `new-from-netlist`, and `apply-netlist`.
- `new-from-netlist` now includes blocking domain-lint checks in its preflight, so invalid 555 inputs fail before project creation instead of after scaffolding a project shell.
- Added `TRS_STEREO_IMPLEMENTATION_INCOMPLETE` for `AudioJack3` connectors that wire both tip and ring without reading as a left/right stereo pair.

---

## Phase 7 — Improve footprint assignment and validation

### Task 7.1 — Audit footprint fallback behavior
- [ ] Find where footprints are selected, defaulted, or guessed.
- [ ] Detect when placeholder-grade footprints are being emitted.

### Task 7.2 — Tighten footprint validation
- [ ] Add validation that symbol class and footprint are broadly compatible.
- [ ] Warn or fail when the footprint looks generic or placeholder-like for a design intended as a concrete build example.

### Task 7.3 — Add test coverage for footprint selection
- [ ] Add tests for:
  - [ ] NE555 DIP/THT or intended package
  - [ ] potentiometer footprint selection
  - [ ] connector footprint selection
  - [ ] MOSFET package selection
  - [ ] audio jack selection

---

## Phase 8 — Fix test fixtures and restore confidence in the suite

### Task 8.1 — Repair block-detection fixture mismatch
- [x] Investigate why block-detection tests reference `TL071` while fixtures only provide `NE5532`.
- [x] Decide whether to:
  - [ ] add the missing TL071 fixture symbol
  - [x] or update the tests to use NE5532 consistently where they consume the named readability fixture
- [x] Make the affected tests green again.

### Task 8.2 — Audit fixture realism
- [x] Review fixture symbol libraries and example netlists for drift from production expectations.
- [x] Remove stale fixtures or clearly mark them as legacy.
- [x] Add comments or naming that make fixture intent obvious.

### Task 8.3 — Add regression tests for reviewed failures
- [x] Reproduce the broken 555 output in a failing test first.
- [x] Reproduce the bypassed amp input-capacitor issue in a failing test first.
- [x] Fix the code only after the regression tests are in place.

Phase 8 notes:
- The named current readability fixture had drifted badly: its README described the reviewed NE5532 amp while `circuit_ir.json` and `baseline_generated.kicad_sch` still represented a passive `TestLib:R` placeholder circuit. That mismatch is now removed.
- Block-detection tests that consume the named readability fixture were updated to assert the real NE5532 role assignments (`J1` input, `J2` output, `C5` input, `R2/R3` feedback, `R5` interstage, `R6/R7` output conditioning) instead of the retired placeholder connector/resistor set.
- The current real-fixture composition test now treats the single known `LAY012` page-balance warning as the tracked baseline condition while still forbidding any additional composition-lint regressions.

---

## Phase 9 — Refactor oversized modules

### Task 9.1 — Identify high-risk “god files”
- [ ] Measure file size, function count, and dependency fan-in/fan-out for:
  - [ ] routing
  - [ ] layout
  - [ ] graphviz snapping/placement
  - [ ] schematic apply/update logic

### Task 9.2 — Split by responsibility
- [ ] Break large modules into smaller units with explicit boundaries.

#### Suggested decomposition ideas
- [ ] `router`: topology inference, path planning, wire emission, cleanup, diagnostics
- [ ] `layout`: block placement, spacing rules, page-fit/clamping, orientation rules
- [ ] `snap`: snap primitives, collision avoidance, symbol-box normalization
- [ ] `sch_apply`: ownership detection, managed-region rewrite, diff planning, write orchestration

### Task 9.3 — Preserve behavior with characterization tests
- [ ] Add or expand tests before each extraction/refactor.
- [ ] Ensure output semantics stay stable while internals change.

---

## Phase 10 — Improve layout/readability heuristics

### Task 10.1 — Define readability rules as code
- [ ] Convert the desired “human-readable schematic” characteristics into measurable checks.

#### Candidate checks
- [ ] minimum spacing between semantic blocks
- [ ] no overlap of symbol bounding boxes
- [ ] decouplers near served IC
- [ ] connectors near page edges
- [ ] left-to-right signal flow for typical single-channel circuits
- [ ] power cluster separated from signal cluster
- [ ] support passives near associated active stage

### Task 10.2 — Tune layout for the reviewed examples
- [ ] Add example-specific expectations for the NE5532 amp.
- [ ] Add example-specific expectations for the 555 dimmer.
- [ ] Use these as regression layouts to tune heuristics.

### Task 10.3 — Improve diagnostics for layout failures
- [ ] Make layout warnings actionable.
- [ ] Include offending symbols / blocks / bounding boxes in diagnostics.
- [ ] Allow tests to assert on layout-quality metrics.

---

## Phase 11 — Documentation and developer ergonomics

### Task 11.1 — Update internal docs for the true supported pipeline
- [ ] Document the canonical generation path.
- [ ] Document that all supported generation must pass post-generation validation.
- [ ] Remove or explicitly label legacy behavior.

### Task 11.2 — Document hard-fail invariants
- [ ] Add a developer-facing section listing non-negotiable invariants, including:
  - [ ] unique pin-to-net membership
  - [ ] valid reparsable schematic output
  - [ ] mandatory domain rules for supported circuit families

### Task 11.3 — Add a debugging guide for generation failures
- [ ] Document how to inspect:
  - [ ] IR
  - [ ] validation output
  - [ ] emitted schematic
  - [ ] post-generation reparse results
  - [ ] layout/readability diagnostics

---

## Phase 12 — Final verification and release criteria

### Task 12.1 — Verify NE5532 output
- [ ] Generate the NE5532 example end-to-end.
- [ ] Confirm the resulting schematic:
  - [ ] reparses cleanly
  - [ ] contains real symbols and wires
  - [ ] has no duplicated pin/net assignments
  - [ ] does not bypass the coupling capacitor
  - [ ] handles unused connector pins explicitly
  - [ ] passes readability checks

### Task 12.2 — Verify 555 output
- [ ] Generate the 555 example end-to-end.
- [ ] Confirm the resulting schematic:
  - [ ] reparses cleanly
  - [ ] contains real symbols and wires
  - [ ] has no duplicated pin/net assignments
  - [ ] has correct 555 PWM topology
  - [ ] has correct MOSFET low-side switch topology
  - [ ] uses sane timing values
  - [ ] passes readability checks

### Task 12.3 — Run full regression suite
- [ ] Run all unit tests.
- [ ] Run all integration / end-to-end tests.
- [ ] Run any fixture-based golden tests.
- [ ] Save before/after artifacts for the reviewed circuits.

### Task 12.4 — Release gate
- [ ] Do not merge until:
  - [ ] block-detection tests are green
  - [ ] new regression tests exist for both reviewed circuits
  - [ ] output-validation hard gates are active
  - [ ] the broken 555 path is fixed or removed
  - [ ] the NE5532 example no longer triggers the reviewed semantic errors

---

## Suggested implementation order for Copilot

1. [ ] Add post-generation reparse validation.
2. [ ] Add unique pin-to-net hard-fail checks.
3. [ ] Find and remove/bypass-proof legacy export paths.
4. [ ] Add failing regression tests for the 555 example.
5. [ ] Fix 555 topology and generation.
6. [ ] Add failing regression tests for the NE5532 example.
7. [ ] Fix amp topology and connector semantics.
8. [ ] Improve layout/readability heuristics.
9. [ ] Repair test fixtures and block-detection failures.
10. [ ] Refactor oversized modules after behavior is protected by tests.

---

## Definition of done

This remediation is done only when all of the following are true:

- [ ] Invalid generated schematics can no longer be emitted as successful outputs.
- [ ] Duplicate pin-to-net membership is rejected automatically.
- [ ] The 555 PWM dimmer is generated as a real, valid, readable schematic.
- [ ] The NE5532 amp is generated without the reviewed semantic mistakes.
- [ ] The reviewed examples have end-to-end regression coverage.
- [ ] The failing fixture/test mismatch is repaired.
- [ ] Layout/readability is materially improved for both reference designs.
- [ ] The supported generation pipeline is documented and enforced.
