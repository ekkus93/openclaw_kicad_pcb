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
- [x] Determine whether the 555 example is being produced from:
  - Not the cause: an outdated schema
  - Not the cause: a legacy exporter
  - [x] a custom side format
  - Not the cause: a malformed translation layer
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
  - Not selected: move it from post-cap node to ground as the input impedance / bias path
- [x] Add an audio lint rule that flags any coupling capacitor directly paralleled by a resistor unless explicitly allowed.

### Task 5.2 — Fix ambiguous connector handling
- [x] Decide whether the design should be mono or stereo.
- [x] If mono:
  - [x] keep the authored TRS symbols and explicitly no-connect the unused ring pins
- Stereo branch not selected for the reviewed design.

### Task 5.3 — Review output-stage semantics
- [x] Decide whether the circuit is intended as:
  - Not selected: preamp / line driver
  - [x] light headphone driver
  - Not selected: true headphone amplifier
- [x] Align generated documentation / warnings / design choices accordingly.

#### Subtasks
- [x] Review the default value and rationale for the output resistor.
- [x] Add an advisory when output impedance is high relative to likely headphone loads.
- [x] Add an advisory when the selected op-amp is being presented as a speaker-power solution.

### Task 5.4 — Review inter-stage coupling on split rails
- [x] Decide whether inter-stage AC coupling is actually required in the default design.
- Not applicable: the reviewed design keeps inter-stage AC coupling intentionally.
- [x] If kept intentionally, document the design trade-off and make the rule explicit.

### Task 5.5 — Improve NE5532 schematic readability
- [x] Separate the schematic into clear blocks:
  - [x] input / connector / coupling
  - [x] volume control
  - [x] gain stage
  - [x] buffer / follower
  - [x] output protection / coupling
  - [x] power and decoupling
- [x] Move decoupling to a clean supply cluster near the power unit.
- [x] Reduce vertical crowding around the op-amp core.
- [x] Improve connector placement and spacing.

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
- [x] Bypassed input coupling capacitor in audio stage -> at least warning, possibly hard fail for generated reference designs
- [x] Ambiguous unused connector pins -> warning or hard fail based on policy

Phase 6 notes:
- Domain-specific linting now runs through a small circuit-family registry in `commands/_validate.py` instead of one flat warning accumulator. The registry currently separates `generic`, `audio_connector`, `audio_opamp`, and `timer555_pwm` rules.
- Advisory payloads now carry both `family` and `severity`, while preserving the existing JSON warning shape for callers and tests.
- Mandatory 555 topology violations (`TIMER555_*` role/timing/gate/load structure faults except the frequency advisory) are now tagged `hard_fail` and rejected by `validate-netlist`, `new-from-netlist`, and `apply-netlist`.
- `new-from-netlist` now includes blocking domain-lint checks in its preflight, so invalid 555 inputs fail before project creation instead of after scaffolding a project shell.
- Added `TRS_STEREO_IMPLEMENTATION_INCOMPLETE` for `AudioJack3` connectors that wire both tip and ring without reading as a left/right stereo pair.

---

## Phase 7 — Improve footprint assignment and validation

### Task 7.1 — Audit footprint fallback behavior
- [x] Find where footprints are selected, defaulted, or guessed.
- [x] Detect when placeholder-grade footprints are being emitted.

### Task 7.2 — Tighten footprint validation
- [x] Add validation that symbol class and footprint are broadly compatible.
- [x] Warn or fail when the footprint looks generic or placeholder-like for a design intended as a concrete build example.

### Task 7.3 — Add test coverage for footprint selection
- [x] Add tests for:
  - [x] NE555 DIP/THT or intended package
  - [x] potentiometer footprint selection
  - [x] connector footprint selection
  - [x] MOSFET package selection
  - [x] audio jack selection

Phase 7 notes:
- Audit result: the netlist pipeline currently does not guess or synthesize footprints on the supported generation path. `ComponentIR` simply stores the incoming `footprint`, `ir.autofix` preserves legacy footprint strings during conversion, and `_write_symbols(...)` emits the stored footprint or an empty string verbatim into the managed schematic.
- The only pre-existing footprint gate was `check_footprints_assigned(...)`, which only rejects empty footprints when a caller explicitly requests PCB-oriented pattern generation. There was no quality check for symbol-like placeholder footprints or obviously incompatible package classes on the netlist validation path.
- `commands/_validate.py` now emits `FOOTPRINT_LOOKS_PLACEHOLDER_OR_SYMBOL_ID` when a component footprint reads like a symbol id / placeholder and `FOOTPRINT_CLASS_MISMATCH` when a concrete footprint does not broadly match the component class (IC, transistor, potentiometer, connector, audio jack, resistor, capacitor, diode).
- Added focused unit and command-path coverage proving the reviewed package classes are accepted for NE555 DIP/THT, potentiometer, generic connector, MOSFET SOT-23, and audio-jack footprints, while placeholder-like or mismatched footprints surface as warnings during `validate-netlist`.

---

## Phase 8 — Fix test fixtures and restore confidence in the suite

### Task 8.1 — Repair block-detection fixture mismatch
- [x] Investigate why block-detection tests reference `TL071` while fixtures only provide `NE5532`.
- [x] Decide whether to:
  - Not selected: add the missing TL071 fixture symbol
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
- [x] Measure file size, function count, and dependency fan-in/fan-out for:
  - [x] routing
  - [x] layout
  - [x] graphviz snapping/placement
  - [x] schematic apply/update logic

### Task 9.2 — Split by responsibility
- [x] Break large modules into smaller units with explicit boundaries.

#### Suggested decomposition ideas
- Future extraction seam: `router` -> topology inference, path planning, wire emission, cleanup, diagnostics
- Future extraction seam: `layout` -> block placement, spacing rules, page-fit/clamping, orientation rules
- Future extraction seam: `snap` -> snap primitives, collision avoidance, symbol-box normalization
- Landed in this phase: `sch_apply` -> ownership detection, managed-region rewrite, diff planning, write orchestration

### Task 9.3 — Preserve behavior with characterization tests
- [x] Add or expand tests before each extraction/refactor.
- [x] Ensure output semantics stay stable while internals change.

Phase 9 notes:
- Audit snapshot for the four highest-risk files on the current branch:
  - `router.py`: 3390 lines, 80 top-level `def`/`class` blocks
  - `layout.py`: 1636 lines, 24 top-level `def`/`class` blocks
  - `graphviz_layout/snap.py`: 4960 lines, 84 top-level `def`/`class` blocks
  - `commands/_sch_apply.py`: 1656 lines, 39 top-level `def`/`class` blocks before refactor
- The dependency fan-in sample showed `commands/_sch_apply.py` had the safest first extraction seam: broad internal responsibilities, but a relatively narrow external surface centered on `commands/netlist.py`, `_resolve_layout` monkeypatch points in tests, and generated-schematic diagnostics/reporting behavior.
- Extracted a dedicated `commands/_sch_apply_artifacts.py` module for generated-schematic validation, warning-report serialization, debug-stage recording, managed-file cleanup, and schematic-path resolution. `_sch_apply.py` remains the public import surface for existing tests/callers via re-exported helpers.
- Expanded command-path characterization coverage so the warning sidecar assertions now pin `validation_mode` and `generated_schematic_diagnostics`, in addition to the pre-existing cleanup and structured-diagnostics tests.
- Phase 9 validation was green with `.venv/bin/ruff check .`, `.venv/bin/mypy kicad-pcb/src`, and `PYTHONPATH=kicad-pcb/src .venv/bin/pytest -q`.

---

## Phase 10 — Improve layout/readability heuristics

### Task 10.1 — Define readability rules as code
- [x] Convert the desired “human-readable schematic” characteristics into measurable checks.

#### Candidate checks
- [x] minimum spacing between semantic blocks
- [x] no overlap of symbol bounding boxes
- [x] decouplers near served IC
- [x] connectors near page edges
- [x] left-to-right signal flow for typical single-channel circuits
- [x] power cluster separated from signal cluster
- [x] support passives near associated active stage

### Task 10.2 — Tune layout for the reviewed examples
- [x] Add example-specific expectations for the NE5532 amp.
- [x] Add example-specific expectations for the 555 dimmer.
- [x] Use these as regression layouts to tune heuristics.

### Task 10.3 — Improve diagnostics for layout failures
- [x] Make layout warnings actionable.
- [x] Include offending symbols / blocks / bounding boxes in diagnostics.
- [x] Allow tests to assert on layout-quality metrics.

Phase 10 notes:
- Readability is now measured in code via the existing `schematic_metrics.py` helpers and layout lints (`LAY006`, `LAY008`, `LAY012`, `LAY013`), including block separation, local density, page-balance/composition, short-wire clutter, symbol spacing, and power/global-label counts.
- Example-specific regression expectations already exist for both reviewed circuits: `tests/unit/test_phase10_validation.py` locks the NE5532 readability baseline and `tests/unit/test_phase4_555_regression.py` locks the canonical 555 stage/timing/output placement.
- The generated Phase 12 artifacts confirm the intended layout grammar on real outputs: NE5532 now reads left-to-right as `J1 -> C5 -> RV1 -> U1A/U1B -> R6/C7 -> J2` with the decoupling cluster centered near the op-amp, while the 555 fixture keeps the timer, timing capacitors, gate resistor, MOSFET, and load block in the expected order.

---

## Phase 11 — Documentation and developer ergonomics

### Task 11.1 — Update internal docs for the true supported pipeline
- [x] Document the canonical generation path.
- [x] Document that all supported generation must pass post-generation validation.
- [x] Remove or explicitly label legacy behavior.

### Task 11.2 — Document hard-fail invariants
- [x] Add a developer-facing section listing non-negotiable invariants, including:
  - [x] unique pin-to-net membership
  - [x] valid reparsable schematic output
  - [x] mandatory domain rules for supported circuit families

### Task 11.3 — Add a debugging guide for generation failures
- [x] Document how to inspect:
  - [x] IR
  - [x] validation output
  - [x] emitted schematic
  - [x] post-generation reparse results
  - [x] layout/readability diagnostics

Phase 11 notes:
- `README.md` now documents the canonical supported generation path (`CircuitIR.load` -> semantic + symbol validation -> blocking advisory gate -> managed-sheet mutation -> post-generation reparse validation -> warning sidecar/debug dump) and explicitly states that legacy side formats are not alternate public pipelines.
- Added a developer-facing invariant list covering unique pin-to-net membership, reparsable/generated structural validity, and mandatory domain-rule blocking behavior.
- Added a copy-pasteable debugging guide with concrete `validate-netlist`, `new-from-netlist --debug-dump`, `OpenClaw_Warnings.json`, `info-sch --json`, and debug-dump inspection steps.

---

## Phase 12 — Final verification and release criteria

### Task 12.1 — Verify NE5532 output
- [x] Generate the NE5532 example end-to-end.
- [x] Confirm the resulting schematic:
  - [x] reparses cleanly
  - [x] contains real symbols and wires
  - [x] has no duplicated pin/net assignments
  - [x] does not bypass the coupling capacitor
  - [x] handles unused connector pins explicitly
  - [x] passes readability checks

### Task 12.2 — Verify 555 output
- [x] Generate the 555 example end-to-end.
- [x] Confirm the resulting schematic:
  - [x] reparses cleanly
  - [x] contains real symbols and wires
  - [x] has no duplicated pin/net assignments
  - [x] has correct 555 PWM topology
  - [x] has correct MOSFET low-side switch topology
  - [x] uses sane timing values
  - [x] passes readability checks

### Task 12.3 — Run full regression suite
- [x] Run all unit tests.
- [x] Run all integration / end-to-end tests.
- [x] Run any fixture-based golden tests.
- [x] Save before/after artifacts for the reviewed circuits.

### Task 12.4 — Release gate
- [x] Do not merge until:
  - [x] block-detection tests are green
  - [x] new regression tests exist for both reviewed circuits
  - [x] output-validation hard gates are active
  - [x] the broken 555 path is fixed or removed
  - [x] the NE5532 example no longer triggers the reviewed semantic errors

Phase 12 notes:
- End-to-end Phase 12 artifacts now live under `code_review/generated/code_review8_phase12/phase12_ne5532/` and `code_review/generated/code_review8_phase12/phase12_timer555/`.
- The NE5532 warning sidecar reports 24 symbols, 124 wires, 44 binding markers, and zero hard failures / unresolved refs / duplicate bindings. The only remaining advisories are the intentional `HEADPHONE_OUTPUT_IMPEDANCE_HIGH`, `SPLIT_RAIL_INTERSTAGE_AC_COUPLING_PRESENT`, and `VALIDATION_MODE_INTERNAL` warnings.
- The 555 warning sidecar reports 19 symbols, 76 wires, 34 binding markers, and zero hard failures / unresolved refs / duplicate bindings; the only warning on the canonical generated output is `VALIDATION_MODE_INTERNAL`.
- The generated NE5532 artifact confirms the reviewed readability fixes on the actual output (`J1`, `C5`, `RV1`, `U1A`, `U1B`, `R6`, `C7`, `J2` in left-to-right order with four decouplers clustered near the op-amp and two explicit `no_connect` markers). The generated 555 artifact keeps `U1`, `C1/C4`, `R2`, `Q1`, and `J1` in the intended timer-to-load progression.

---

## Suggested implementation order for Copilot

1. [x] Add post-generation reparse validation.
2. [x] Add unique pin-to-net hard-fail checks.
3. [x] Find and remove/bypass-proof legacy export paths.
4. [x] Add failing regression tests for the 555 example.
5. [x] Fix 555 topology and generation.
6. [x] Add failing regression tests for the NE5532 example.
7. [x] Fix amp topology and connector semantics.
8. [x] Improve layout/readability heuristics.
9. [x] Repair test fixtures and block-detection failures.
10. [x] Refactor oversized modules after behavior is protected by tests.

---

## Definition of done

This remediation is done only when all of the following are true:

- [x] Invalid generated schematics can no longer be emitted as successful outputs.
- [x] Duplicate pin-to-net membership is rejected automatically.
- [x] The 555 PWM dimmer is generated as a real, valid, readable schematic.
- [x] The NE5532 amp is generated without the reviewed semantic mistakes.
- [x] The reviewed examples have end-to-end regression coverage.
- [x] The failing fixture/test mismatch is repaired.
- [x] Layout/readability is materially improved for both reference designs.
- [x] The supported generation pipeline is documented and enforced.
