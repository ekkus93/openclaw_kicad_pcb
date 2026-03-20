# SCHEMATIC_FIXES_TODO1.md

## Goal

Improve the OpenClaw KiCad schematic-generation skill so that the generated schematic for the op-amp headphone amplifier is both:

1. electrically correct relative to the intended design, and
2. readable in the way a competent human analog designer would expect.

This TODO is based on review of:
- the generated KiCad schematic PNG,
- the generated KiCad schematic files,
- the `OpAmp_Audio_Amp_notes.txt` design notes,
- the `opamp_audio_headphone_amp_left_netlist.json` netlist,
- and the current OpenClaw skill code behavior.

This document is written as an implementation plan for GitHub Copilot. It is intentionally explicit.

## Status legend

- `DONE` — completed and reflected in the current repo/docs state
- `IN PROGRESS` — partially implemented, analyzed, or planned in enough detail to continue directly
- `NOT STARTED` — not yet implemented for this fix pass

---

## Priority order

Implement in this order:

1. **Fix correctness blockers**
  Status: `IN PROGRESS`
   - multi-unit op-amp handling
   - suspicious / likely incorrect netlist interpretation around `R1` and `C5`
2. **Add analog-aware placement and grouping**
  Status: `IN PROGRESS`
3. **Reduce routing clutter**
  Status: `IN PROGRESS`
4. **Improve page composition and readability**
  Status: `IN PROGRESS`
5. **Add tests, fixtures, and regression protection**
  Status: `IN PROGRESS`

---

## Phase 0 - Read and understand the current pipeline

Status: `IN PROGRESS`

### 0.1 Identify the relevant pipeline entry points
Status: `DONE`
- Find the code path that:
  - reads or constructs the normalized circuit/netlist representation,
  - maps devices to KiCad symbols,
  - lays out symbols,
  - computes symbol pin anchor points,
  - routes wires,
  - emits `.kicad_sch` output.
- Make a written note in code comments or local dev notes for:
  - the primary entry point function,
  - the graph/layout module,
  - the routing module,
  - the KiCad emitter module,
  - any symbol-library or multi-unit-device handling layer.

### 0.2 Trace how `U1`, `R1`, `C5`, `R2`, `R3`, `R6`, `R7`, `J1`, and `J2` are represented internally
Status: `DONE`
- Log or inspect the internal representation for the op-amp amplifier example.
- Confirm:
  - whether multi-unit symbols are modeled at all,
  - whether the system knows that `NE5532` is dual,
  - whether unit selection is explicit or implicit,
  - how connector pins are represented,
  - how local ground/power symbols are inserted,
  - whether the router sees hyperedges, pairwise edges, or net trees.

Current findings:
- The current concrete fixture candidate is `code_review/ne5532_headphone_amp_netlist.json`.
- `U1` is represented as a single `ComponentIR` ref with symbol `Amplifier_Operational:NE5532`; stage identity is implicit in pin numbers, not explicit in unit metadata.
- The current IR schema includes `PinRefIR.unit`, but the active validation path rejects it, so multi-unit selection is not modeled in the live generation pipeline.
- `J1` and `J2` are represented as `Connector:AudioJack3` symbols using explicit connector pins such as `T` and `S`.
- Local power/ground symbols are not authored in the input netlist; they are introduced later by routing/emission logic.
- The router consumes named nets with multi-pin memberships and derives route plans from those net groups rather than from explicit pairwise edges in the source JSON.

### 0.3 Create a developer fixture for the failing example
Status: `IN PROGRESS`
- Add a stable fixture input for this exact amplifier example.
- Include:
  - the design notes file,
  - the JSON netlist,
  - expected node names,
  - expected component list.
- Ensure the fixture can be run deterministically in a test or local CLI mode.

#### Concrete file map for fixture creation

Status: `DONE`

Implement the fixture work in this order.

1. **Choose the authoritative input artifacts**
   - Primary truth for this fix pass:
     - the headphone-amp notes artifact when available under a stable repo path
     - the authoritative JSON netlist for the left-channel op-amp headphone amp
   - Current repo reality to account for:
     - the review names `OpAmp_Audio_Amp_notes.txt` and `opamp_audio_headphone_amp_left_netlist.json`, but those exact files are not currently present in the repo under those names
     - the closest current repo netlist artifact is `code_review/ne5532_headphone_amp_netlist.json`
   - Before adding new fixture files, verify whether an existing repo fixture already captures the intended amplifier example under another name

2. **Add a stable fixture directory under tests**
   - Preferred location:
     - `tests/fixtures/readability/` if this work is treated as schematic-readability regression coverage
     - otherwise another existing fixture subtree under `tests/fixtures/` that already hosts generator/regression inputs
   - The fixture directory should contain:
     - the JSON netlist used as input truth
     - the notes/design-intent artifact when available
     - a short fixture readme or metadata file naming the example and intent
     - expected key net names
     - expected key component refs

3. **Formalize expected structural metadata**
   - Add a small machine-readable or copy-pasteable metadata artifact near the fixture that records:
     - expected component refs such as `U1`, `R1`, `C5`, `R2`, `R3`, `R6`, `R7`, `J1`, `J2`
     - expected important net names such as `LEFT_IN`, `IN_L_AC`, `VOL_L_OUT`, and output-stage nets
     - any fixture-specific expectations, including the fact that this case currently contains a suspicious `R1` / `C5` topology that should warn rather than be silently corrected

4. **Make the fixture runnable from tests and local CLI workflows**
   - Add or update tests so the fixture can be passed through:
     - validation
     - project generation
     - managed-schematic generation
   - Prefer existing command/integration test homes rather than inventing a parallel harness
   - If a small helper script is needed for local developer reproduction, keep it under `scripts/` and make it explicitly fixture-targeted

### 0.4 Save the current output as a regression baseline
Status: `IN PROGRESS`
- Preserve the current generated `.kicad_sch` and/or PNG output as a “before” artifact for comparison.
- Do **not** treat the current output as correct.
- Use it only as a baseline to show improvement.

#### Concrete file map for baseline preservation

Status: `DONE`

Implement the before-baseline work in this order.

1. **Capture the current generated output for the authoritative fixture**
   - Preserve the current generated artifacts for the same input netlist/notes pair chosen above
   - Baseline artifacts should include, when available:
     - generated managed schematic
     - generated root schematic
     - preview PNG and/or SVG
     - any existing layout metrics already used in tests or review scripts

2. **Store the baseline under a stable test/review path**
   - Preferred location:
     - inside the same fixture directory under `tests/fixtures/...` when the baseline is tightly coupled to one named fixture
     - or under an existing readability-regression fixture subtree if that pattern is already established in the repo
   - Keep the baseline clearly labeled as:
     - current output
     - before artifact
     - not correctness truth

3. **Add comparison metadata rather than relying only on raw images**
   - Preserve or generate lightweight metadata near the baseline such as:
     - wire count
     - junction count
     - selected layout-lint counts
     - notable structural observations
   - Use these as soft regression guardrails later; do not treat the current baseline as the desired final result

4. **Wire the baseline into regression tests and review tooling**
   - Update or add tests/scripts so developers can:
     - regenerate the fixture output
     - compare it to the preserved before baseline
     - inspect both structural assertions and readability metrics
   - Prefer existing homes first:
     - `tests/unit/` or `tests/integration/` for structural assertions
     - existing readability-regression fixture tooling if present
     - `scripts/` for developer-only comparison helpers

### Minimum must-change paths for Phase 0.3 and 0.4

- `tests/fixtures/` subtree for the named amplifier fixture and preserved before baseline
- existing command/integration test files under `tests/` that exercise validation and generation from the fixture
- `scripts/` only if a small developer reproduction/comparison helper is actually needed
- `code_review/` may keep human-review notes, but durable automated fixture inputs and baselines should live under `tests/fixtures/`

### Implementation notes for Phase 0.3 and 0.4

- Do **not** treat the current generated schematic or preview as correctness truth.
- Do **not** leave the authoritative fixture only in ad hoc session directories or external workspace paths.
- Prefer repo-local durable fixture assets over references to transient session artifacts.
- If the exact review-named notes/netlist files are unavailable, formalize the closest authoritative existing repo artifacts first and document the naming mismatch explicitly.

---

## Phase 1 - Fix correctness blockers

Status: `IN PROGRESS`

## 1.1 Implement proper multi-unit symbol support

Status: `IN PROGRESS`

### Problem
The circuit uses both halves of an `NE5532`, but the generated schematic is not representing this clearly as distinct units such as `U1A` and `U1B`.

### Required result
The internal model and KiCad output must support multi-unit devices properly.

### Concrete change map for device-vs-unit support

Status: `DONE`

Implement the multi-unit work in this dependency order. Do **not** spend major effort on routing polish until steps 1 through 5 are complete.

1. **IR and validation layer**
   - Update `kicad-pcb/src/kicad_pcb/ir/validate.py`
     - remove the current MVP rule that rejects `PinRefIR.unit`
     - validate that explicit unit references resolve correctly
     - validate that referenced pins belong to the selected unit
   - Review `kicad-pcb/src/kicad_pcb/circuit_ir.py`
     - keep `PinRefIR.unit` as the IR-level hook for unit-aware nets
     - add clarifying comments or helpers if needed
   - Update `kicad-pcb/src/kicad_pcb/commands/_validate.py` and `kicad-pcb/src/kicad_pcb/commands/netlist.py` only as needed so unit-related validation failures surface clearly in CLI output

2. **Internal device-vs-unit expansion layer**
   - Update `kicad-pcb/src/kicad_pcb/commands/_sch_apply.py`
     - stop treating every `ComponentIR.ref` as exactly one placed drawable symbol
     - introduce or call a helper that expands a physical/logical device like `U1` into placed units like `U1A`, `U1B`, and optional power unit
     - ensure each placed unit carries:
       - parent device ref
       - KiCad unit number / unit identity
       - symbol id
       - pin subset
       - placement identity
       - mapping back to the parent device
   - Prefer a dedicated internal helper module under `kicad-pcb/src/kicad_pcb/` if that keeps `_sch_apply.py` readable; do not add a new top-level package for this

3. **Symbol metadata layer**
   - Extend `kicad-pcb/src/kicad_pcb/lib_symbol.py`
     - expose total unit count for a symbol
     - expose pin membership by unit
     - expose per-unit pin geometry where KiCad stores it separately
     - detect whether a separate power unit exists
     - expose the unit numbering KiCad expects in emitted schematic instances
   - Extend `kicad-pcb/src/kicad_pcb/symbol_index.py`
     - cache unit-aware symbol metadata, not just a flat pin set
     - support both whole-symbol validation and per-unit validation

4. **Placement and tiering layer**
   - Update `kicad-pcb/src/kicad_pcb/layout_engine.py`
     - make the placement interface capable of returning placements for placed units, not just raw component refs
   - Update `kicad-pcb/src/kicad_pcb/tier.py`
     - upgrade existing unit-group detection into real tiering/sibling-constraint support for placed units
   - Update `kicad-pcb/src/kicad_pcb/graphviz_layout/__init__.py`
     - place `U1A` and `U1B` as separate graph/layout nodes
     - keep sibling units visually related
     - prefer left-to-right stage order for this fixture: `U1A` then `U1B`
     - place a power unit outside the main signal-flow chain only when the symbol library requires it
   - Update `kicad-pcb/src/kicad_pcb/layout.py`
     - compute orientations per placed unit rather than per parent device

5. **Pin-anchor and routing layer**
   - Update `kicad-pcb/src/kicad_pcb/commands/_sch_apply.py`
     - compute pin endpoints using the placed unit and its pin subset
     - stop keying all endpoints only by `(component.ref, pin)` where that causes ambiguity across units
   - Update `kicad-pcb/src/kicad_pcb/router.py`
     - route against placed-unit pin anchors
     - preserve existing readability work while making unit ownership explicit
     - ensure feedback/local nets attach to the correct op-amp stage pins

6. **KiCad emitter layer**
   - Update `kicad-pcb/src/kicad_pcb/sch_doc/nodes.py`
     - `make_symbol_node(...)` must accept and emit the real KiCad unit number instead of hardcoding `(unit 1)`
     - the `(instances (project ... (path ... (unit N))))` block must also emit the correct unit number
   - Update `kicad-pcb/src/kicad_pcb/sch_doc/__init__.py`
     - `SchematicDoc.add_symbol(...)` must accept unit-aware arguments and pass them through to the node builder

7. **Fixture and regression layer**
   - Add or formalize the headphone-amp regression fixture before relying on the multi-unit changes as complete
   - Update tests after the structural model is in place:
     - command/integration coverage for unit-aware generation
     - unit-aware pin validation coverage
     - placement assertions for distinct `U1A` / `U1B`
     - routing assertions proving nets connect to the correct unit pins

### Minimum must-change files for Phase 1 multi-unit support

- `kicad-pcb/src/kicad_pcb/ir/validate.py`
- `kicad-pcb/src/kicad_pcb/lib_symbol.py`
- `kicad-pcb/src/kicad_pcb/symbol_index.py`
- `kicad-pcb/src/kicad_pcb/commands/_sch_apply.py`
- `kicad-pcb/src/kicad_pcb/tier.py`
- `kicad-pcb/src/kicad_pcb/graphviz_layout/__init__.py`
- `kicad-pcb/src/kicad_pcb/layout_engine.py`
- `kicad-pcb/src/kicad_pcb/layout.py`
- `kicad-pcb/src/kicad_pcb/router.py`
- `kicad-pcb/src/kicad_pcb/sch_doc/nodes.py`
- `kicad-pcb/src/kicad_pcb/sch_doc/__init__.py`

### Tasks

#### 1.1.1 Audit current symbol-instance modeling
Status: `DONE`
- Determine whether the current code treats every reference designator as a single placed drawable object.
- Identify where the assumption “one refdes = one drawn symbol body” exists.
- Document all places where this assumption affects:
  - placement,
  - pin lookup,
  - routing,
  - symbol emission.

#### 1.1.2 Add an internal concept of “device” vs “placed unit”
Status: `NOT STARTED`
- Introduce or formalize two different concepts:
  - **physical/logical device**: e.g. `U1`
  - **placed unit**: e.g. `U1A`, `U1B`, optionally power unit
- Ensure each placed unit has:
  - unit number / subunit identity,
  - symbol library reference,
  - pin subset,
  - placement coordinates,
  - orientation,
  - mapping back to parent device.

#### 1.1.3 Add symbol metadata for multi-unit parts
Status: `IN PROGRESS`
- Ensure the symbol lookup layer can answer:
  - total number of units,
  - which pins belong to each unit,
  - whether there is a separate power unit,
  - how KiCad expects unit numbering to be emitted.
- If the current symbol parsing layer does not expose this, extend it.

#### 1.1.4 Split `NE5532` into separate drawable units
Status: `NOT STARTED`
- For the headphone amp example, `U1` must produce:
  - one drawable symbol for stage 1 op-amp,
  - one drawable symbol for stage 2 op-amp,
  - and, if the chosen KiCad symbol library requires it, a separate power unit.
- Ensure each routed net attaches to the correct unit pins.

#### 1.1.5 Update placement to operate on placed units, not just parent devices
Status: `IN PROGRESS`
- The layout engine must place `U1A` and `U1B` separately.
- It must still know they belong to the same parent device.
- Add optional constraints for sibling units:
  - same vertical band or nearby placement,
  - ordered left-to-right for signal flow when appropriate,
  - power unit near or above/below the main units if drawn.

#### 1.1.6 Update routing to use placed-unit pin anchors
Status: `DONE`
- Ensure pin anchor calculations use the correct placed unit and pin subset.
- Eliminate any routing ambiguity caused by shared parent device state.

#### 1.1.7 Update KiCad emitter for unit-aware symbol instances
Status: `NOT STARTED`
- Emit the correct unit information into `.kicad_sch`.
- Verify the output opens cleanly in KiCad without silently collapsing units or misassigning pins.

#### 1.1.8 Add tests for multi-unit parts
Status: `IN PROGRESS`
- Add tests for:
  - dual op-amp split into two units,
  - routing to correct pins,
  - stable distinct placement for both units,
  - power unit handling if applicable.

### Phase 1.1 first-slice progress

Status: `IN PROGRESS`

- The first vertical slice is now implemented in the generation path.
- `kicad-pcb/src/kicad_pcb/lib_symbol.py` exposes KiCad unit pin groups from flattened symbol metadata, which is enough to derive the `NE5532` unit split from the inherited `LM2904` sub-symbols.
- `kicad-pcb/src/kicad_pcb/symbol_index.py` now caches both flat symbol pins and per-unit pin maps, so validation and generation no longer re-read KiCad unit metadata separately.
- `kicad-pcb/src/kicad_pcb/commands/_sch_apply.py` now expands a single device ref into explicit placed-unit refs for generation (`U1A`, `U1B`, and `U1P` for the real system `NE5532` fixture) and emits only the unit-local pin subset for each placed symbol.
- `kicad-pcb/src/kicad_pcb/lib_symbol.py` and `kicad-pcb/src/kicad_pcb/symbol_index.py` now expose and cache unit-local pin geometry, not just unit pin membership, so placed-unit endpoint calculation can use the exact KiCad sub-symbol coordinates.
- `kicad-pcb/src/kicad_pcb/layout.py` now accepts optional placed-pin subsets during orientation calculation, so fallback orientation heuristics ignore any net memberships outside the placed unit's own pins.
- `kicad-pcb/src/kicad_pcb/layout_engine.py` now documents placement outputs in terms of placed refs, so the layout contract explicitly allows multi-unit results such as `U1A` and `U1P` rather than only parent-device refs.
- `kicad-pcb/src/kicad_pcb/tier.py` now exposes signal-unit sibling metadata for multi-unit groups and can derive ordered sibling constraints that exclude a power-only unit from the main signal chain.
- `kicad-pcb/src/kicad_pcb/graphviz_layout/__init__.py` and `kicad-pcb/src/kicad_pcb/graphviz_layout/dot_builder.py` now pass and emit invisible sibling-order constraints so signal units like `U1A` and `U1B` stay visually related while a power unit remains outside that chain.
- `kicad-pcb/src/kicad_pcb/commands/_sch_apply.py` now resolves pin endpoints from unit-local geometry for each placed symbol and passes the placed pin subset into fallback orientation computation instead of relying on whole-symbol metadata.
- `kicad-pcb/src/kicad_pcb/commands/_sch_apply.py` now returns explicit `PinAnchor` ownership metadata for each placed pin, and `kicad-pcb/src/kicad_pcb/router.py` now routes against that richer anchor map instead of inferring known pins only from the flattened endpoint dictionary.
- `kicad-pcb/src/kicad_pcb/sch_doc/nodes.py` and `kicad-pcb/src/kicad_pcb/sch_doc/__init__.py` now write the actual KiCad `unit` number into both the placed symbol node and the instance path metadata instead of hardcoding unit `1`.
- Regression coverage now exists for:
  - unit metadata extraction via `TestLib:DualOpAmp`
  - unit-local pin geometry extraction in `tests/unit/test_sch_doc.py`
  - cached unit metadata lookup in `tests/unit/test_symbol_index.py`
  - helper-level device-to-unit expansion and unit-local placed-pin geometry resolution in `tests/unit/test_sch_apply.py`
  - router-level anchor consumption coverage proving `route_nets(...)` can consume explicit pin anchors even when the legacy endpoint map is empty
  - tier-level sibling-constraint coverage and DOT emission coverage for signal units versus power units in `tests/unit/test_phase4_layout.py`
  - placed-pin-subset-aware orientation fallback in `tests/unit/test_phase9_orientation.py`
  - command-level `validate-netlist` coverage for explicit `PinRefIR.unit` acceptance and rejection in `tests/unit/test_netlist_commands.py`
  - command-level `apply-netlist` / `new-from-netlist` coverage proving explicit `PinRefIR.unit` reaches generation and binds onto `U1A`, plus mirrored failure coverage for mismatched unit/pin input, in `tests/unit/test_netlist_commands.py`
  - command-level real-system NE5532 placement in `tests/unit/test_netlist_commands.py`
- `kicad-pcb/src/kicad_pcb/ir/validate.py` now accepts explicit `PinRefIR.unit` only when the symbol exposes KiCad unit metadata, rejects unknown unit ids, and rejects pins that do not belong to the selected unit.
- Still pending in later Phase 1.1 slices:
  - router-side consumption of the richer placed-unit anchor model beyond endpoint generation
  - broader layout/routing refinements for the real NE5532 readability fixture

---

## 1.2 Fix or explicitly validate the `R1` / `C5` input network

Status: `IN PROGRESS`

### Problem
The current netlist appears to place `R1` directly across `C5`, which is suspicious and likely not the intended input-coupling topology.

### Required result
The tool must either:
- correct the source interpretation if the netlist was derived incorrectly, or
- preserve the netlist exactly but flag the topology as suspicious for review.

### Concrete change map for the `R1` / `C5` warning work

Status: `DONE`

Implement the suspicious-topology work in this dependency order. Default behavior for this fix pass is: preserve the extracted netlist unless upstream intent is provably different, and emit a warning rather than silently correcting the circuit.

1. **Fixture and evidence capture**
   - Add or formalize the authoritative headphone-amp fixture first
   - Ensure the fixture includes:
     - the notes artifact when available
     - the JSON netlist used as input truth
     - expected component list
     - expected important net names, including `LEFT_IN` and `IN_L_AC`
   - Preserve the current generated schematic/preview as a before-artifact baseline for comparison only

2. **IR inspection and topology-probe helpers**
   - Add focused topology-inspection helpers in the validation/lint layer rather than burying this logic in layout or routing
   - The first required capability is: given a pair of nets such as `LEFT_IN` and `IN_L_AC`, identify all components bridging exactly those two nets and classify them by type/value/symbol
   - A second required capability is: identify local analog motifs such as
     - coupling capacitor in series with the signal path
     - resistor directly in parallel with that capacitor
     - output capacitor followed by bleed/load resistor
     - feedback resistor touching an op-amp output and inverting input neighborhood

3. **Validation / warning layer**
   - Update `kicad-pcb/src/kicad_pcb/commands/_validate.py`
     - extend non-fatal warning collection so suspicious analog-topology findings can be surfaced alongside existing advisory warnings
   - Update `kicad-pcb/src/kicad_pcb/commands/netlist.py`
     - ensure `validate-netlist`, `new-from-netlist`, and `apply-netlist` can surface these warnings cleanly in command results
   - Update `kicad-pcb/src/kicad_pcb/results.py` if additional structured warning payload fields are needed for CLI/JSON output

4. **Topology rule implementation**
   - Add the actual analog-topology warning logic in a dedicated validation/linting location, not in the router
   - Preferred existing homes to evaluate first:
     - `kicad-pcb/src/kicad_pcb/commands/_validate.py` for lightweight advisory warnings
     - `kicad-pcb/src/kicad_pcb/preflight.py` if the rule fits the existing pre-generation checking posture
     - `kicad-pcb/src/kicad_pcb/lint/` if the warnings should become a more formal named lint family
   - The implementation must at minimum detect and warn on:
     - resistor directly paralleled with a coupling capacitor on an input path
     - output coupling capacitor bypassed by a low-resistance path
     - missing or non-local feedback topology around an op-amp stage
     - ambiguous connector usage when unused pins are left without explicit treatment
   - For this specific fix pass, the first concrete rule should be the `R1` / `C5` parallel-topology warning on `LEFT_IN` ↔ `IN_L_AC`

5. **Optional upstream-correction path**
   - Only pursue upstream correction if the source notes and extraction path clearly prove the intended topology differs from the JSON netlist
   - If an upstream notes-to-netlist extraction path exists in this repo or adjacent tooling, update that source instead of mutating the schematic generator to silently reinterpret the circuit
   - If no trustworthy upstream correction path exists, stop at warnings and keep the schematic generator electrically faithful to the netlist

6. **Warning surfacing and metadata**
   - Ensure warnings are visible in:
     - CLI text output
     - structured command results / JSON output
     - logs or generated metadata if the current pipeline already supports that path
   - Do not make these warnings fatal by default for this fix pass

7. **Regression tests**
   - Add focused tests that prove:
     - the actual headphone-amp fixture triggers the expected `R1` / `C5` warning when the suspicious topology is present
     - non-suspicious coupling networks do not trigger the warning
     - warnings survive command-level validation and generation paths
   - Prefer hard assertions on warning presence/absence and soft assertions on warning text details

### Minimum must-change files for Phase 1.2 warning support

- `kicad-pcb/src/kicad_pcb/commands/_validate.py`
- `kicad-pcb/src/kicad_pcb/commands/netlist.py`
- `kicad-pcb/src/kicad_pcb/results.py` if warning payload structure needs extension
- `kicad-pcb/src/kicad_pcb/preflight.py` and/or `kicad-pcb/src/kicad_pcb/lint/` depending on where the analog warning rule is formalized
- fixture and regression test files under `tests/` for the headphone-amp case

### Implementation notes for this fix pass

- Do **not** hide the issue by altering layout, routing, or symbol placement.
- Do **not** silently rewrite the electrical meaning of the netlist in the schematic generator.
- The first deliverable is trustworthy warning behavior for the existing netlist.
- Any upstream correction path must be explicitly proven from source notes and extraction logic, not inferred from schematic aesthetics.

### Tasks

#### 1.2.1 Confirm the exact current connectivity
Status: `DONE`
- Trace these nets from the JSON netlist:
  - `LEFT_IN`
  - `IN_L_AC`
- Verify whether:
  - `C5` is between those two nets,
  - `R1` is also between those same two nets.

Current findings from `code_review/ne5532_headphone_amp_netlist.json`:
- `LEFT_IN` = `J1.T`, `C5.1`, `R1.1`
- `IN_L_AC` = `C5.2`, `R1.2`, `RV1.1`
- Therefore `C5` is between `LEFT_IN` and `IN_L_AC`.
- Therefore `R1` is also between `LEFT_IN` and `IN_L_AC`.
- This confirms the current netlist encodes `R1` directly in parallel with `C5` across the input coupling boundary.

#### 1.2.2 Compare the netlist to the design notes
Status: `DONE`
- Read the design notes carefully and determine the intended role of `R1`.
- Decide whether `R1` was intended to be:
  - input impedance to ground,
  - post-cap bias resistor to ground,
  - cap bleed resistor,
  - or a direct shunt across the capacitor.
- Record the discrepancy clearly in comments or dev notes.

Current findings:
- A workspace-local copy of the missing notes artifact was located at `/home/ubo/.openclaw/media/inbound/71f077cc-46d8-4973-8dd8-c93dbc7cf165.txt`.
- Agent session provenance also shows the original notes were written as `/home/ubo/kicad-projects/OpAmp_Audio_Amp/OpAmp_Audio_Amp_notes.txt` and later reused to derive the netlist.
- The notes explicitly describe `C5` as a series coupling capacitor between `LEFT_IN` and `IN_L_AC` and also explicitly describe `R1` as `100 kΩ from node LEFT_IN to node IN_L_AC`.
- The notes later restate `R1` as optional: `R1: 100 kΩ from LEFT_IN to IN_L_AC (optional; sets input impedance; can omit if using pot directly)`.
- Therefore the current JSON netlist matches the authored notes on this point; the suspicious `R1` / `C5` parallel topology originates in the source notes/design artifact itself, not in a later discrepancy between notes and netlist.

#### 1.2.3 Find where the netlist was derived from the notes
Status: `DONE`
- Identify the code or prompt layer that produced the JSON netlist from the notes.
- Determine whether the issue is:
  - source-note ambiguity,
  - parser/LLM netlist extraction error,
  - or a later normalization bug.

Current findings:
- Agent session logs under `/home/ubo/.openclaw/agents/main/sessions/` capture the provenance chain.
- Session `02fa958c-f707-416d-8f4d-21d5893703ca.jsonl` shows the notes being authored and written to `/home/ubo/kicad-projects/OpAmp_Audio_Amp/OpAmp_Audio_Amp_notes.txt`.
- The same session family also shows the richer intermediate artifact `audio_headphone_amp_netlist.json` being generated from those notes, with explicit typed components, polarity metadata, and unused-ring semantics.
- Session `ba280840-d8b6-4627-9bda-5043c11bb665.jsonl` shows a later user message attaching the same notes text and asking for a netlist JSON, followed by delivery of `opamp_audio_headphone_amp_left_netlist.json` copied from a fixed session artifact.
- Across the located intermediate JSONs and the repo fixture candidate, the `R1` / `C5` topology is preserved consistently.
- Current evidence therefore points to source-note intent or ambiguity, not a later normalization/transformation bug inside the schematic generator.

#### 1.2.4 Decide and implement one of these two behaviors
Status: `IN PROGRESS`

##### Option A: fix the netlist-generation logic
If the notes clearly imply a different intended topology:
- update the notes-to-netlist extraction logic,
- regenerate the amplifier fixture,
- verify the corrected netlist.

##### Option B: preserve the netlist but add topology warnings
If exact correction cannot be guaranteed:
- keep the netlist faithful,
- add a warning pass that flags suspicious constructs such as:
  - resistor in parallel with a coupling capacitor on an input path,
  - direct DC bypass of intended AC coupling,
  - improbable analog topologies.

Current implementation status:
- `kicad-pcb/src/kicad_pcb/commands/_validate.py` now emits non-fatal advisory warnings for:
  - `INPUT_COUPLING_BYPASSED_BY_RESISTOR`
  - `OUTPUT_COUPLING_BYPASSED_BY_RESISTOR`
  - `CONNECTOR_UNUSED_PINS_AMBIGUOUS`
- These warnings now surface through `validate-netlist`, `apply-netlist`, and `new-from-netlist` via the existing structured `warnings` result path.

#### 1.2.5 Add validation for suspicious analog topologies
Status: `DONE`
- Implement a lightweight rule checker that can flag:
  - coupling capacitor directly paralleled by a resistor,
  - output coupling capacitor bypassed by low-value path,
  - feedback network not touching inverting input,
  - op-amp output floating or directly shorted into a rail,
  - connector pins left ambiguously floating.
- Make warnings non-fatal unless configured otherwise.

Current findings:
- The warning checker is now implemented and wired into command results for all planned Phase 1.2 warning-family cases:
  - coupling capacitor directly paralleled by a resistor on an input path
  - output coupling capacitor paralleled by a resistor on an output path
  - multi-pin connector symbols with unaccounted-for pins
  - op-amp stages whose local feedback path does not touch the inverting-input net
  - op-amp output pins that are floating or land directly on rail-like nets
- Running `validate-netlist` against `code_review/ne5532_headphone_amp_netlist.json` with the real KiCad symbol libraries yields exactly three advisory warnings:
  - `INPUT_COUPLING_BYPASSED_BY_RESISTOR` once, for `C5` paralleled by `R1` between `LEFT_IN` and `IN_L_AC`
  - `CONNECTOR_UNUSED_PINS_AMBIGUOUS` twice, once each for `J1` and `J2` because the `R` pin on `Connector:AudioJack3` is intentionally unused in this left-channel-only netlist
- The real NE5532 fixture does **not** currently trigger:
  - `OUTPUT_COUPLING_BYPASSED_BY_RESISTOR`
  - `OPAMP_FEEDBACK_MISSING_OR_NONLOCAL`
  - `OPAMP_OUTPUT_FLOATING`
  - `OPAMP_OUTPUT_SHORTED_TO_RAIL`
- Warning codes now include:
  - `INPUT_COUPLING_BYPASSED_BY_RESISTOR`
  - `OUTPUT_COUPLING_BYPASSED_BY_RESISTOR`
  - `CONNECTOR_UNUSED_PINS_AMBIGUOUS`
  - `OPAMP_FEEDBACK_MISSING_OR_NONLOCAL`
  - `OPAMP_OUTPUT_FLOATING`
  - `OPAMP_OUTPUT_SHORTED_TO_RAIL`

#### 1.2.6 Add tests for the `R1/C5` case
Status: `DONE`
- Add at least one test that reproduces the suspicious topology.
- Verify:
  - it is either corrected upstream,
  - or a warning is emitted downstream.

Current findings:
- Added a matching helper-layer Phase 1 warning suite in `tests/unit/test_sch_apply.py` that mirrors the command suite structure:
  - synthetic advisory-warning fixtures for each warning family (`input-coupling`, `output-coupling`, `connector-ambiguity`, `missing-feedback`, `output-floating`, `output-shorted-to-rail`)
  - helper-level drift protection for the real `code_review/ne5532_headphone_amp_netlist.json` fixture
- Added a broader Phase 1 warning suite in `tests/unit/test_netlist_commands.py` that now groups:
  - synthetic `validate-netlist` fixtures for each warning family (`input-coupling`, `output-coupling`, `connector-ambiguity`, `missing-feedback`, `output-floating`, `output-shorted-to-rail`)
  - command-level drift protection for the real `code_review/ne5532_headphone_amp_netlist.json` fixture
- The real-fixture assertion still checks the exact current normalized warning set against the system KiCad symbol libraries:
  - `INPUT_COUPLING_BYPASSED_BY_RESISTOR` once for `C5` / `R1`
  - `CONNECTOR_UNUSED_PINS_AMBIGUOUS` twice for `J1` and `J2`
- Added command-level warning coverage in `tests/unit/test_netlist_commands.py` so `validate-netlist`, `apply-netlist`, and `new-from-netlist` preserve warning surfacing for suspicious analog topologies.
- Ran both aligned suites together as a single reusable Phase 1 gate:
  - `python -m pytest -q tests/unit/test_sch_apply.py tests/unit/test_netlist_commands.py -k 'Phase1WarningSuite or real_ne5532_fixture_warning_set or synthetic_warning_fixtures_cover_each_phase1_family or feedback_warning_not_emitted_for_local_feedback_bridge'`
  - Result: `15 passed` for the combined helper + command warning gate, with `ruff check tests/unit/test_sch_apply.py tests/unit/test_netlist_commands.py` also green.
- Routine pre-merge checklist for Phase 1 warning work:
  - run `python -m pytest -q tests/unit/test_sch_apply.py tests/unit/test_netlist_commands.py -k 'Phase1WarningSuite or real_ne5532_fixture_warning_set or synthetic_warning_fixtures_cover_each_phase1_family or feedback_warning_not_emitted_for_local_feedback_bridge'`
  - run `python -m ruff check tests/unit/test_sch_apply.py tests/unit/test_netlist_commands.py`
  - treat failures in either suite as helper/command warning drift until proven otherwise

---

## Phase 2 - Improve analog-aware grouping and placement

Status: `IN PROGRESS`

## 2.1 Add functional-block detection for analog schematics

Status: `IN PROGRESS`

### Problem
The current result is better than before but still mostly reflects generic graph layout rather than intentional analog schematic drafting.

### Required result
The layout engine should identify and place analog functional blocks.

### Tasks

#### 2.1.1 Add block classification rules
Status: `IN PROGRESS`
Implement heuristics to classify structures such as:
- **input block**
  - connector/jack
  - series coupling capacitor
  - input impedance/bias elements
  - potentiometer/volume control
- **gain stage**
  - op-amp unit
  - feedback resistor(s)
  - inverting-node network
- **interstage coupling block**
  - coupling capacitor between stages
  - bias resistor to ground or reference
- **buffer/output stage**
  - op-amp follower or driver stage
- **output conditioning block**
  - output resistor
  - coupling capacitor
  - bleed/load resistor
  - output connector
- **power/decoupling block**
  - rail symbols
  - local decoupling capacitors
  - local ground returns

#### 2.1.2 Build block membership from graph motifs
Status: `IN PROGRESS`
- Use graph patterns to infer membership:
  - op-amp output back to inverting input through a resistor = feedback member
  - series cap between connector and active node = input coupling member
  - resistor from output-side node to ground near connector = bleed/load member
  - capacitor from rail to ground near IC = decoupling member
- Prefer deterministic rules over vague heuristics when possible.

#### 2.1.3 Add block-level layout constraints
Status: `IN PROGRESS`
- Place blocks in natural signal-flow order from left to right:
  - input
  - stage 1
  - interstage
  - stage 2
  - output
- Place power/decoupling above and around the active device(s), not as a disconnected island.

---

## 2.2 Add op-amp-specific placement rules

Status: `IN PROGRESS`

### Problem
Feedback and stage topology are not visually obvious.

### Required result
An op-amp stage should look like a human-drawn op-amp stage.

### Tasks

#### 2.2.1 Non-inverting stage layout
Status: `IN PROGRESS`
For a non-inverting amplifier:
- place the op-amp triangle pointing right,
- place the non-inverting input path coming from the left,
- place the inverting input feedback node below or near the lower input,
- place feedback resistor close to the op-amp output and inverting input,
- place gain-to-ground resistor directly below the inverting input node.

#### 2.2.2 Voltage follower / buffer layout
Status: `IN PROGRESS`
For a unity-gain buffer:
- place the op-amp with clear feedback from output directly to inverting input,
- place the incoming signal at the non-inverting input,
- keep the local loop very short and visually obvious.

#### 2.2.3 Keep stage-local parts close
Status: `IN PROGRESS`
- Add strong constraints that:
  - `R2` hugs `U1A`,
  - `R3` hangs locally from the inverting node to ground,
  - any direct output-to-inverting feedback wire is short,
  - `C6` and `R5` sit between stage 1 output and stage 2 input,
  - `R6`, `C7`, `R7`, and `J2` form one right-side chain.

#### 2.2.4 Avoid stretching feedback loops across large distances
Status: `IN PROGRESS`
- Penalize placements where feedback members are far from their op-amp unit.
- Add explicit cost terms or hard constraints for:
  - op-amp output to feedback resistor distance,
  - feedback resistor to inverting input distance,
  - inverting node to shunt resistor-to-ground distance.

---

## 2.3 Place decoupling correctly

Status: `IN PROGRESS`

### Problem
The decoupling network is visually too far from the op-amp.

### Required result
Decoupling must appear local to the IC it serves.

### Tasks

#### 2.3.1 Detect decoupling components
Status: `IN PROGRESS`
- Identify capacitors that connect from supply rails to ground near active devices.
- Associate them with the nearest relevant active device, especially op-amps.

#### 2.3.2 Add local-decoupling placement rules
Status: `IN PROGRESS`
- Place positive-rail decouplers above the op-amp unit area.
- Place negative-rail decouplers below or near the lower rail area.
- Keep the ground symbol local to those capacitors.
- Ensure the decoupling cluster reads as attached to the op-amp, not floating elsewhere.

#### 2.3.3 Draw rail connections cleanly
Status: `IN PROGRESS`
- Prefer short vertical or horizontal rail drops.
- Avoid meandering rail wires for local decouplers.

---

## 2.4 Improve connector handling

Status: `IN PROGRESS`

### Problem
The TRS connectors are left-channel-only in usage, but the unused ring behavior is not very clear.

### Required result
Connectors should be explicit and unambiguous.

### Tasks

#### 2.4.1 Decide on left-channel-only symbol strategy
Status: `IN PROGRESS`
Implement one of:
- use mono connector symbols where appropriate,
- or keep TRS symbols but mark unused pins explicitly,
- or add generator configuration to choose between full connector and simplified channel-specific representation.

#### 2.4.2 Mark unused pins explicitly
Status: `NOT STARTED`
- If TRS symbols remain, emit no-connect markers on unused pins where appropriate.
- Avoid leaving the reader guessing whether a pin was forgotten.

#### 2.4.3 Improve connector orientation and attachment
Status: `IN PROGRESS`
- Input connector should clearly face into the circuit from the left.
- Output connector should clearly face out of the circuit on the right.
- Avoid awkward connector placement that hides signal flow.

---

## Phase 3 - Reduce routing clutter

Status: `IN PROGRESS`

## 3.1 Prefer placement that eliminates routing complexity

Status: `IN PROGRESS`

### Problem
The current routing looks too busy for a small analog circuit.

### Required result
A simple analog circuit should have calm, short, obvious wiring.

### Tasks

#### 3.1.1 Rebalance placement vs routing
Status: `IN PROGRESS`
- Move complexity reduction earlier into placement.
- Prefer placing related parts close enough that routing becomes trivial.
- Do not rely on elaborate router behavior to compensate for weak placement.

#### 3.1.2 Penalize excessive bends and junctions
Status: `IN PROGRESS`
- Add routing cost penalties for:
  - extra bends,
  - unnecessary jogs,
  - long orthogonal detours,
  - hub-and-spoke routing when a short direct route would do,
  - avoidable junction proliferation.

#### 3.1.3 Add a “small analog circuit” routing mode
Status: `NOT STARTED`
- For compact analog circuits, prefer:
  - short local direct routes,
  - one or two bends max for local nets,
  - minimal trunk/spine use,
  - minimal labels unless needed.

---

## 3.2 Add net-class-specific routing preferences

Status: `IN PROGRESS`

### Problem
All nets appear to be treated too generically.

### Required result
Different net types should route differently.

### Tasks

#### 3.2.1 Classify nets
Status: `IN PROGRESS`
Classify nets into categories such as:
- signal-chain nets,
- feedback nets,
- shunt-to-ground nets,
- power nets,
- connector-only nets,
- local decoupling nets.

#### 3.2.2 Route by net class
Status: `IN PROGRESS`
- **feedback nets**: shortest and most local possible
- **shunt-to-ground nets**: prefer short vertical drop to nearby ground
- **power nets**: clean local rail presentation
- **signal-chain nets**: left-to-right readable path
- **connector nets**: short clean attachment to connector pins

#### 3.2.3 Prefer labels only when they improve clarity
Status: `IN PROGRESS`
- Avoid label fallback for short readable local nets.
- Use labels only when they reduce crossing/clutter or improve comprehension.

---

## 3.3 Improve ground presentation

Status: `IN PROGRESS`

### Problem
Grounded passive parts are not always presented in the clearest analog style.

### Required result
Grounded shunt parts should be visually obvious.

### Tasks

#### 3.3.1 Add local ground-drop preference
Status: `IN PROGRESS`
- For resistors/caps that terminate to ground:
  - prefer placing the grounded end downward,
  - add a local ground symbol directly below,
  - avoid long runs to distant common ground points.

#### 3.3.2 Keep stage-local grounds stage-local in drawing
Status: `IN PROGRESS`
- Do not over-centralize grounds in the visual layout.
- Preserve clarity over theoretical “single common ground symbol” compactness.

---

## Phase 4 - Improve page composition

Status: `IN PROGRESS`

## 4.1 Use the sheet intentionally

Status: `IN PROGRESS`

### Problem
The circuit occupies only part of the page and does not look composed.

### Required result
The schematic should look intentionally arranged on the page.

### Tasks

#### 4.1.1 Add page-level packing / centering
Status: `IN PROGRESS`
- After block placement, compute the overall circuit bounding box.
- Recenter and scale spacing so the main circuit occupies a balanced region of the sheet.
- Avoid leaving most of the page empty unless the design is truly tiny.

#### 4.1.2 Respect title block exclusion zone
Status: `IN PROGRESS`
- Add or improve a keep-out region around the title block.
- Ensure no meaningful circuitry crowds or overlaps that visual area.

#### 4.1.3 Keep power block and main circuit visually connected
Status: `IN PROGRESS`
- Power/decoupling may be above the main path, but it should still read as part of the same design.

---

## 4.2 Improve block spacing and alignment

Status: `IN PROGRESS`

### Problem
The stage boundaries are not strong enough visually.

### Required result
Blocks should align clearly and read left-to-right.

### Tasks

#### 4.2.1 Align major signal-path nodes horizontally
Status: `IN PROGRESS`
- Input block, stage 1, stage 2, and output block should share a coherent horizontal axis where appropriate.

#### 4.2.2 Use consistent spacing between blocks
Status: `IN PROGRESS`
- Add spacing rules for:
  - within-block compactness,
  - between-block separation,
  - power-block offset,
  - connector margin from page edge.

#### 4.2.3 Keep local loops compact
Status: `IN PROGRESS`
- Feedback loop and buffer loop should remain much tighter than the spacing between major functional blocks.

---

## 4.3 Add optional important net labels

Status: `IN PROGRESS`

### Problem
Internal nodes are harder to inspect than they need to be.

### Required result
Important named nets can be shown when helpful.

### Tasks

#### 4.3.1 Decide label policy
Status: `IN PROGRESS`
- Add configuration for:
  - minimal labels,
  - debug labels,
  - always-show-important-labels.

#### 4.3.2 Identify important nets for display
Status: `NOT STARTED`
For this example, consider exposing:
- `LEFT_IN`
- `IN_L_AC`
- `VOL_L_OUT`
- `OUT_L_STAGE1`
- `BUF_L_IN`
- `HP_L_OUT`

#### 4.3.3 Avoid label overuse
Status: `IN PROGRESS`
- Only place labels where they improve reading or debugging.
- Do not replace good local wiring with gratuitous labels.

---

## Phase 5 - Add validation, tests, and regression protection

Status: `IN PROGRESS`

## 5.1 Add schematic-readability regression fixtures

Status: `IN PROGRESS`

### Required result
The quality improvements should stay fixed.

### Tasks

#### 5.1.1 Add this amplifier as a named fixture
Status: `IN PROGRESS`
- Store the amplifier example as a durable regression fixture.

#### 5.1.2 Add expected structural assertions
Status: `IN PROGRESS`
Assert that:
- there are two distinct drawable op-amp units for `U1`,
- decoupling is associated with the op-amp region,
- output chain members are ordered logically,
- feedback parts are near their op-amp stage,
- unused connector pins are handled explicitly.

#### 5.1.3 Add route-quality metrics
Status: `IN PROGRESS`
Track and compare:
- wire count,
- bend count,
- junction count,
- average local-net length,
- max feedback-loop span.

Do not overfit to exact numbers, but enforce sane upper bounds.

---

## 5.2 Add topology warnings / linting

Status: `NOT STARTED`

### Required result
The tool should help catch suspicious circuits before drawing them prettily.

### Tasks

#### 5.2.1 Add analog lint rules
Status: `NOT STARTED`
Warn on:
- coupling capacitor directly paralleled by resistor,
- missing op-amp feedback,
- output capacitor followed by no defined load/bleed path,
- unconnected connector pins without explicit no-connect,
- decoupling parts far from active device in layout phase,
- likely mistaken stage topology.

#### 5.2.2 Add warning surfacing
Status: `NOT STARTED`
- Ensure warnings can appear in:
  - CLI output,
  - logs,
  - generated metadata,
  - optional sidecar report.

---

## 5.3 Add before/after comparison tooling

Status: `IN PROGRESS`

### Required result
Developers should be able to see whether layout quality actually improved.

### Tasks

#### 5.3.1 Save render snapshots during tests or dev mode
Status: `IN PROGRESS`
- Generate PNG or equivalent preview renders for:
  - current baseline,
  - improved output.

#### 5.3.2 Add a schematic-quality review script
Status: `NOT STARTED`
- Create a simple developer utility that:
  - generates the fixture,
  - reports warnings,
  - prints key layout metrics,
  - saves the result to a known output folder.

---

## Phase 6 - Optional but strongly recommended cleanup

Status: `IN PROGRESS`

## 6.1 Separate generic graph heuristics from analog-specific drafting heuristics
Status: `NOT STARTED`
- Refactor so generic placement logic is not tangled with analog-special-case logic.
- Keep analog rules in a clear module or strategy layer.

## 6.2 Add schematic-style profiles
Status: `NOT STARTED`
- Add output profiles such as:
  - generic digital,
  - analog audio,
  - power supply,
  - dense debug.
- Use analog-audio profile for this circuit.

## 6.3 Improve internal debug introspection
Status: `IN PROGRESS`
- Add optional debug dumps for:
  - block classification,
  - unit splitting,
  - net classification,
  - placement constraints,
  - final route choices.

---

## Acceptance criteria

The work is complete when the generated schematic for the op-amp headphone amp satisfies all of the following:

1. `NE5532` is drawn as proper separate units for the two amplifier stages.
2. Feedback topology for the first stage is immediately obvious.
3. Buffer topology for the second stage is immediately obvious.
4. Decoupling is visually local to the op-amp.
5. The output chain reads clearly from op-amp to resistor to capacitor to output jack.
6. Grounded shunt elements drop locally to ground in a readable way.
7. The connector treatment is explicit and not ambiguous.
8. The page composition looks intentional and avoids title-block crowding.
9. Routing clutter is materially lower than in the current output.
10. The suspicious `R1/C5` topology is either fixed upstream or flagged clearly by validation.
11. The result opens cleanly in KiCad and remains stable under regeneration.
12. Regression tests exist so these improvements are not lost.

---

## Suggested implementation sequence for Copilot

1. Add fixture and trace current pipeline.
2. Implement device-vs-unit internal model.
3. Make emitter and router unit-aware.
4. Add op-amp analog placement rules.
5. Add decoupling placement rules.
6. Add output-chain and connector rules.
7. Add net-class-aware routing simplifications.
8. Add topology linting for suspicious analog patterns.
9. Add page composition pass.
10. Add tests and regression metrics.
