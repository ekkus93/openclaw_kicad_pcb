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
  Status: `DONE`
2. **Add analog-aware placement and grouping**
  Status: `IN PROGRESS`
3. **Reduce routing clutter**
  Status: `IN PROGRESS`
4. **Improve page composition and readability**
  Status: `DONE`
5. **Add tests, fixtures, and regression protection**
  Status: `DONE`

---

## Phase 0 - Read and understand the current pipeline

Status: `DONE`

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
Status: `DONE`
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

Current findings:
- The checked-in fixture READMEs under `tests/fixtures/readability/ne5532_headphone_amp_left_current/` and `tests/fixtures/readability/ne5532_headphone_amp_left_regressed/` now record the authoritative key refs, key nets, and fixture-specific expectations.
- Those expectations now explicitly document that `J1` and `J2` are mono-left TRS connectors whose ring pins are intentionally unused and should emit explicit KiCad `no_connect` markers in managed schematics.
- The `R1` / `C5` topology remains documented as authored input truth that may warn during validation but must not be silently rewritten by generation.

4. **Make the fixture runnable from tests and local CLI workflows**
   - Add or update tests so the fixture can be passed through:
     - validation
     - project generation
     - managed-schematic generation
   - Prefer existing command/integration test homes rather than inventing a parallel harness
   - If a small helper script is needed for local developer reproduction, keep it under `scripts/` and make it explicitly fixture-targeted

### 0.4 Save the current output as a regression baseline
Status: `DONE`
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

Status: `DONE`

## 1.1 Implement proper multi-unit symbol support

Status: `DONE`

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
Status: `DONE`
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
Status: `DONE`
- Ensure the symbol lookup layer can answer:
  - total number of units,
  - which pins belong to each unit,
  - whether there is a separate power unit,
  - how KiCad expects unit numbering to be emitted.
- If the current symbol parsing layer does not expose this, extend it.

Current audit findings:
- `kicad-pcb/src/kicad_pcb/lib_symbol.py` and `kicad-pcb/src/kicad_pcb/symbol_index.py` already expose unit-numbered pin membership (`read_lib_symbol_unit_pins(...)`, `SymbolIndex.get_unit_pins(...)`) and unit-local pin geometry (`read_lib_symbol_unit_pin_at(...)`, `SymbolIndex.get_unit_pin_at(...)`). The returned unit keys also match the KiCad unit numbers currently emitted downstream.
- `kicad-pcb/src/kicad_pcb/lib_symbol.py` now also exposes `read_lib_symbol_power_unit(...)`, and `kicad-pcb/src/kicad_pcb/symbol_index.py` now caches that metadata via `SymbolIndex.get_power_unit(...)`, so the symbol-definition layer can explicitly identify a dedicated power-only unit instead of relying only on later net-usage heuristics.

#### 1.1.4 Split `NE5532` into separate drawable units
Status: `DONE`
- For the headphone amp example, `U1` must produce:
  - one drawable symbol for stage 1 op-amp,
  - one drawable symbol for stage 2 op-amp,
  - and, if the chosen KiCad symbol library requires it, a separate power unit.
- Ensure each routed net attaches to the correct unit pins.

#### 1.1.5 Update placement to operate on placed units, not just parent devices
Status: `DONE`
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
Status: `DONE`
- Emit the correct unit information into `.kicad_sch`.
- Verify the output opens cleanly in KiCad without silently collapsing units or misassigning pins.

#### 1.1.8 Add tests for multi-unit parts
Status: `DONE`
- Add tests for:
  - dual op-amp split into two units,
  - routing to correct pins,
  - stable distinct placement for both units,
  - power unit handling if applicable.

### Phase 1.1 first-slice progress

Status: `DONE`

- The first vertical slice is now implemented in the generation path.
- `kicad-pcb/src/kicad_pcb/lib_symbol.py` exposes KiCad unit pin groups from flattened symbol metadata, which is enough to derive the `NE5532` unit split from the inherited `LM2904` sub-symbols.
- `kicad-pcb/src/kicad_pcb/symbol_index.py` now caches both flat symbol pins and per-unit pin maps, so validation and generation no longer re-read KiCad unit metadata separately.
- `kicad-pcb/src/kicad_pcb/commands/_sch_apply.py` now expands a single device ref into explicit placed-unit refs for generation (`U1A`, `U1B`, and `U1P` for the real system `NE5532` fixture) and emits only the unit-local pin subset for each placed symbol.
- `kicad-pcb/src/kicad_pcb/lib_symbol.py` and `kicad-pcb/src/kicad_pcb/symbol_index.py` now expose and cache unit-local pin geometry, not just unit pin membership, so placed-unit endpoint calculation can use the exact KiCad sub-symbol coordinates.
- `kicad-pcb/src/kicad_pcb/lib_symbol.py` and `kicad-pcb/src/kicad_pcb/symbol_index.py` now also expose and cache explicit dedicated power-unit metadata from the symbol definition itself via `read_lib_symbol_power_unit(...)` / `SymbolIndex.get_power_unit(...)`.
- `kicad-pcb/src/kicad_pcb/layout.py` now accepts optional placed-pin subsets during orientation calculation, so fallback orientation heuristics ignore any net memberships outside the placed unit's own pins.
- `kicad-pcb/src/kicad_pcb/layout_engine.py` now documents placement outputs in terms of placed refs, so the layout contract explicitly allows multi-unit results such as `U1A` and `U1P` rather than only parent-device refs.
- `kicad-pcb/src/kicad_pcb/tier.py` now exposes signal-unit sibling metadata for multi-unit groups and can derive ordered sibling constraints that exclude a power-only unit from the main signal chain.
- `kicad-pcb/src/kicad_pcb/graphviz_layout/__init__.py` and `kicad-pcb/src/kicad_pcb/graphviz_layout/dot_builder.py` now pass and emit invisible sibling-order constraints so signal units like `U1A` and `U1B` stay visually related while a power unit remains outside that chain.
- `kicad-pcb/src/kicad_pcb/graphviz_layout/snap.py` now re-enforces split-unit sibling cohesion in the final post-layout coordinates, compacting ordered signal units into adjacent x-lanes and re-centering a power-only unit over that cluster after the late locality/composition passes.
- `kicad-pcb/src/kicad_pcb/commands/_sch_apply.py` now resolves pin endpoints from unit-local geometry for each placed symbol and passes the placed pin subset into fallback orientation computation instead of relying on whole-symbol metadata.
- `kicad-pcb/src/kicad_pcb/commands/_sch_apply.py` now returns explicit `PinAnchor` ownership metadata for each placed pin, and `kicad-pcb/src/kicad_pcb/router.py` now routes against that richer anchor map instead of inferring known pins only from the flattened endpoint dictionary.
- `kicad-pcb/src/kicad_pcb/sch_doc/nodes.py` and `kicad-pcb/src/kicad_pcb/sch_doc/__init__.py` now write the actual KiCad `unit` number into both the placed symbol node and the instance path metadata instead of hardcoding unit `1`.
- Regression coverage now exists for:
  - unit metadata extraction via `TestLib:DualOpAmp`
  - unit-local pin geometry extraction in `tests/unit/test_sch_doc.py`
  - dedicated power-unit metadata extraction and caching in `tests/unit/test_sch_doc.py` and `tests/unit/test_symbol_index.py`
  - cached unit metadata lookup in `tests/unit/test_symbol_index.py`
  - helper-level device-to-unit expansion and unit-local placed-pin geometry resolution in `tests/unit/test_sch_apply.py`
  - router-level anchor consumption coverage proving `route_nets(...)` can consume explicit pin anchors even when the legacy endpoint map is empty
  - tier-level sibling-constraint coverage and DOT emission coverage for signal units versus power units in `tests/unit/test_phase4_layout.py`
  - placed-pin-subset-aware orientation fallback in `tests/unit/test_phase9_orientation.py`
  - command-level `validate-netlist` coverage for explicit `PinRefIR.unit` acceptance and rejection in `tests/unit/test_netlist_commands.py`
  - command-level `apply-netlist` / `new-from-netlist` coverage proving explicit `PinRefIR.unit` reaches generation and binds onto `U1A`, plus mirrored failure coverage for mismatched unit/pin input, in `tests/unit/test_netlist_commands.py`
  - command-level real-system NE5532 placement in `tests/unit/test_netlist_commands.py`
- `kicad-pcb/src/kicad_pcb/ir/validate.py` now accepts explicit `PinRefIR.unit` only when the symbol exposes KiCad unit metadata, rejects unknown unit ids, and rejects pins that do not belong to the selected unit.
- command-level real-fixture coverage now also locks the authoritative `code_review/ne5532_headphone_amp_netlist.json` path to explicit `U1A`, `U1B`, and `U1P` output plus the expected stage/power net bindings, so the real headphone-amp correctness target is no longer only implied by the synthetic system-library NE5532 regression.
- phase-4 coordinator coverage now also locks the final post-snap sibling behavior in `tests/unit/test_phase4_layout.py`, proving ordered signal siblings compact into adjacent x-lanes, the power-only unit recenters over that cluster, and incomplete placed-unit position sets remain a no-op instead of failing.
- Still pending in later Phase 1.1 slices:
  - router-side consumption of the richer placed-unit anchor model beyond endpoint generation
  - broader layout/routing refinements for the real NE5532 readability fixture

---

## 1.2 Fix or explicitly validate the `R1` / `C5` input network

Status: `DONE`

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
Status: `DONE`

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
- Chosen path: **Option B**.
- The source notes and the derived JSON netlist agree that `R1` sits directly between `LEFT_IN` and `IN_L_AC`, so there is no justified upstream correction to make in the schematic generator for this fix pass.
- `kicad-pcb/src/kicad_pcb/commands/_validate.py` now emits non-fatal advisory warnings for:
  - `INPUT_COUPLING_BYPASSED_BY_RESISTOR`
  - `OUTPUT_COUPLING_BYPASSED_BY_RESISTOR`
  - `CONNECTOR_UNUSED_PINS_AMBIGUOUS`
- These warnings now surface through `validate-netlist`, `apply-netlist`, and `new-from-netlist` via the existing structured `warnings` result path.
- Focused validation remains green for the warning path via `tests/unit/test_netlist_commands.py`, including the real `code_review/ne5532_headphone_amp_netlist.json` drift guard.

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

Status: `DONE`

### Problem
The current result is better than before but still mostly reflects generic graph layout rather than intentional analog schematic drafting.

### Required result
The layout engine should identify and place analog functional blocks.

### Tasks

#### 2.1.1 Add block classification rules
Status: `DONE`
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

Current findings:
- `kicad-pcb/src/kicad_pcb/block_detection.py` already exposes the Phase 2 role vocabulary needed by downstream layout and routing: `INPUT`, `PRECONDITIONING`, `OPAMP_CORE`, `FEEDBACK`, `INTERSTAGE`, `BUFFER_STAGE`, `OUTPUT`, `OUTPUT_CONDITIONING`, `POWER_ENTRY`, and `DECOUPLING`.
- The classifier now covers the concrete analog structures called out here: connectors and input coupling parts, op-amp cores and feedback members, interstage coupling plus stage-handoff bias/load parts, explicit follower stages, output-conditioning chains, and local rail-decoupling support.
- Focused regression coverage in `tests/unit/test_block_detection.py` already locks these families on both synthetic circuits and the canonical NE5532 regression fixture.

#### 2.1.2 Build block membership from graph motifs
Status: `DONE`
- Use graph patterns to infer membership:
  - op-amp output back to inverting input through a resistor = feedback member
  - series cap between connector and active node = input coupling member
  - resistor from output-side node to ground near connector = bleed/load member
  - capacitor from rail to ground near IC = decoupling member
- Prefer deterministic rules over vague heuristics when possible.

Current findings:
- `kicad-pcb/src/kicad_pcb/block_detection.py` now builds functional-block membership from explicit net-graph motifs instead of only broad net-name or reference heuristics. The current classifier detects follower stages from unit-aware op-amp pin roles, interstage coupling from active-to-active handoff nets, output-conditioning chains from connector-facing coupling and bleed/load motifs, and local decoupling from rail-to-ground support patterns.
- The authoritative NE5532 regression fixture already lands in the intended membership buckets under `tests/unit/test_block_detection.py`: `C6` / `R5` classify as `INTERSTAGE`, `R6` / `C7` / `R7` classify as `OUTPUT_CONDITIONING`, `R2` / `R3` classify as `FEEDBACK`, and `C1`-`C4` classify as `DECOUPLING`.
- The focused block-detection regression suite currently passes green (`python -m pytest -q tests/unit/test_block_detection.py`), so the remaining Phase 2 work is downstream placement policy rather than unfinished membership inference.

#### 2.1.3 Add block-level layout constraints
Status: `DONE`
- Place blocks in natural signal-flow order from left to right:
  - input
  - stage 1
  - interstage
  - stage 2
  - output
- Place power/decoupling above and around the active device(s), not as a disconnected island.

Current findings:
- `kicad-pcb/src/kicad_pcb/graphviz_layout/snap.py` now strengthens the major left-to-right block-ordering pass for IC-anchored layouts instead of treating them as a no-op. The core block stays fixed while the outer input-side and output-side groups are shifted independently so their x-gaps around the anchored core stay within the readable major-block range.
- This keeps the op-amp locality, feedback, and decoupling invariants from the earlier snap passes intact while still enforcing clearer input → core → output ordering at the block level.
- A follow-on transition-band pass now preserves explicit downstream sub-band ordering after the later deoverlap/property-text snaps: `OPAMP_CORE → INTERSTAGE → BUFFER_STAGE → OUTPUT_CONDITIONING → OUTPUT` stays readable when space allows, and falls back to compressed but still strictly ordered bands near the right page edge.
- Focused regression coverage now exists at both levels: helper coverage in `tests/unit/test_phase8_layout.py` for anchored core-gap normalization plus transition sub-band ordering, a full post-layout snap regression in `tests/unit/test_phase4_layout.py`, and a real-fixture managed-schematic regression in `tests/unit/test_block_detection.py` that asserts the canonical NE5532 interstage/output-conditioning chain stays between the core cluster and the output connector.
- Final closeout validation is green across the full supporting slice: `python -m pytest -q tests/unit/test_block_detection.py tests/unit/test_phase4_layout.py tests/unit/test_phase8_layout.py tests/unit/test_netlist_commands.py tests/unit/test_phase10_validation.py` passed after the split-unit cohesion/cache follow-up, so the remaining open work now moves to the op-amp-specific placement rules in Phase 2.2 rather than block-level ordering.

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

Current progress note:
- `kicad-pcb/src/kicad_pcb/graphviz_layout/snap.py` now adds `_snap_opamp_stage_non_inverting_input_node_shape(...)`, which detects a canonical `OPAMP_CORE` non-inverting input motif with one preconditioning bridge element and one grounded shunt element, then reapplies it late so the bridge into `U1A` stays on the stage row while the local shunt hangs one row below in the same input-node column.
- The matcher intentionally treats a pot wiper or similar bridge element as valid even when it also touches ground, as long as the true shunt element is the ref whose only other connection is ground; this keeps the real `RV1` / `R4` / `U1A` motif readable without misclassifying the pot as the grounded shunt.
- Added focused helper and real-fixture regressions in `tests/unit/test_phase4_layout.py` and `tests/unit/test_netlist_commands.py` that lock the `RV1` bridge-on-row plus `R4` shunt-below node shape for the real NE5532 gain stage. Because this late snap changes final managed coordinates without changing the DOT source, `kicad-pcb/src/kicad_pcb/graphviz_layout/cache.py` now bumps `_LAYOUT_ALGORITHM_REVISION` to invalidate stale persisted layout caches.
- `kicad-pcb/src/kicad_pcb/graphviz_layout/snap.py` now also adds `_snap_opamp_stage_upstream_input_bundle(...)`, plus `_opamp_stage_upstream_bundle(...)` and `_place_opamp_stage_upstream_bundle(...)`, so upstream bridge parts such as the real `C5` / `R1` pair form one compact left-hand column feeding the `RV1` / `R4` input node instead of staying on half-row offsets from the generic input-stage lane spread. The resulting readable chain is `C5/R1 -> RV1/R4 -> R2/R3 -> U1A`, with the real fixture allowed to clamp that left bundle against the page margin.
- Added a new helper regression and a new real-fixture regression in `tests/unit/test_phase4_layout.py` and `tests/unit/test_netlist_commands.py` that lock the full U1A gain-stage column pattern, and bumped `kicad-pcb/src/kicad_pcb/graphviz_layout/cache.py` again to `graphviz-layout-v5` because this new late pass changes final managed coordinates without changing the Graphviz DOT input.

#### 2.2.2 Voltage follower / buffer layout
Status: `IN PROGRESS`
For a unity-gain buffer:
- place the op-amp with clear feedback from output directly to inverting input,
- place the incoming signal at the non-inverting input,
- keep the local loop very short and visually obvious.

Current progress note:
- `kicad-pcb/src/kicad_pcb/router.py` now recognizes a generic 3-pin `BUFFER_STAGE` follower net where two pins belong to the same op-amp unit and the third pin is the first downstream output-support element. Under the existing small-analog-routing policy, that net now routes as an explicit compact local feedback loop plus output branch instead of relying on a generic compact chain.
- Added a routing-layer regression in `tests/unit/test_phase6_wire_simplification.py` for the generic follower motif and a real-fixture regression in `tests/unit/test_netlist_commands.py` that checks the managed NE5532 schematic still draws a compact local U1B feedback jog before the `R6` branch. Ruff plus full `pytest -q` validation passed.
- `kicad-pcb/src/kicad_pcb/graphviz_layout/snap.py` now adds `_snap_buffer_stage_input_node_shape(...)`, which detects the canonical `BUFFER_STAGE` input motif of one interstage bridge plus one grounded shunt and reapplies it late so the handoff bridge stays on the U1B row while the shunt support hangs one row below in the same input-node column.
- Added helper and real-fixture regressions in `tests/unit/test_phase4_layout.py` and `tests/unit/test_netlist_commands.py` that lock the bridge-plus-shunt input-node shape for the NE5532 stage-2 handoff, and updated the older real-fixture row tests so they now assert the intentional one-row shunt drop instead of the previous flattened-row expectation.

#### 2.2.3 Keep stage-local parts close
Status: `DONE`
- Add strong constraints that:
  - `R2` hugs `U1A`,
  - `R3` hangs locally from the inverting node to ground,
  - any direct output-to-inverting feedback wire is short,
  - `C6` and `R5` sit between stage 1 output and stage 2 input,
  - `R6`, `C7`, `R7`, and `J2` form one right-side chain.

Current findings:
- `kicad-pcb/src/kicad_pcb/graphviz_layout/snap.py` now includes explicit post-layout stage-cohesion passes for both input and output staging.
- `_snap_input_stage_cohesion(...)` now keeps input connectors and preconditioning parts in compact left-side lanes, and longer preconditioning chains can use an inner lane near the op-amp instead of collapsing into one flat column.
- `_snap_output_stage_cohesion(...)` now keeps output connectors and output-support parts in compact right-side lanes, and longer output chains can use an inner op-amp-side support lane plus an outer connector-side support lane.
- These lane refinements are covered in `tests/unit/test_phase4_layout.py`, including explicit regressions for longer input-side and output-side chains.

Current progress note:
- Implemented two narrow, late post-layout passes in `kicad-pcb/src/kicad_pcb/graphviz_layout/snap.py` that enforce stage-local compactness for the buffer/output chain: `_snap_buffer_stage_direct_output_support` and `_snap_buffer_stage_output_tail_locality`.
- Added focused helper and real-fixture regressions in `tests/unit/test_phase4_layout.py` and `tests/unit/test_netlist_commands.py` that validate U1B buffer-row shortness and compact output-tail locality. Focused test slices and ruff checks passed during validation.

#### 2.2.4 Avoid stretching feedback loops across large distances
Status: `DONE`
- Penalize placements where feedback members are far from their op-amp unit.
- Add explicit cost terms or hard constraints for:
  - op-amp output to feedback resistor distance,
  - feedback resistor to inverting input distance,
  - inverting node to shunt resistor-to-ground distance.

Current findings:
- The recent snap-pipeline work deliberately preserved the existing Phase 4 rule that core feedback parts stay in the op-amp column instead of being pushed into the new input/output support lanes.
- Focused regressions in `tests/unit/test_phase4_layout.py` still assert that feedback support remains vertically local to the op-amp body while the input/output lane refinements only affect PRECONDITIONING and OUTPUT support staging.

Current progress note:
- Reapplied explicit feedback-node shaping at the end of the final snap pipeline to ensure feedback spans remain compact after the new U1B locality passes.
- Added a focused regression that verifies U1A feedback spans remain within tightened bounds after late-stage locality passes; helper and real-fixture runs were validated green.

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

Current findings:
- The generator now emits explicit KiCad `no_connect` markers on unused connector pins during schematic generation.
- Existing TRS symbols for the headphone-amp fixture therefore no longer leave the unused ring pins visually ambiguous.
- The remaining decision is product-level policy, not basic schematic clarity: whether this fixture should keep the current TRS-plus-no-connect presentation or later switch to a simpler mono/channel-specific symbol strategy.

#### 2.4.2 Mark unused pins explicitly
Status: `DONE`
- If TRS symbols remain, emit no-connect markers on unused pins where appropriate.
- Avoid leaving the reader guessing whether a pin was forgotten.

Current findings:
- `kicad-pcb/src/kicad_pcb/commands/_sch_apply.py` now emits KiCad `no_connect` markers for unused connector pins using the already-computed pin endpoint geometry.
- `kicad-pcb/src/kicad_pcb/sch_doc/nodes.py` and `kicad-pcb/src/kicad_pcb/sch_doc/__init__.py` now support explicit `(no_connect ...)` AST nodes through `make_no_connect_node(...)` and `SchematicDoc.add_no_connect(...)`.
- Focused regression coverage now exists in `tests/unit/test_sch_doc.py` and `tests/unit/test_netlist_commands.py`, including the real-system NE5532 command path asserting two no-connect markers for the unused TRS ring pins on `J1` and `J2`.

#### 2.4.3 Improve connector orientation and attachment
Status: `IN PROGRESS`
- Input connector should clearly face into the circuit from the left.
- Output connector should clearly face out of the circuit on the right.
- Avoid awkward connector placement that hides signal flow.

Current findings:
- Output-stage connector placement in `kicad-pcb/src/kicad_pcb/graphviz_layout/snap.py` now adds one extra snap step of outward clearance beyond the nominal output connector lane.
- Focused coverage in `tests/unit/test_phase4_layout.py` now asserts that output connectors remain the outermost lane and keep at least that extra clearance, which prevents the left-facing output connector stub from collapsing back into the nearest output-support body column.
- The remaining connector-orientation work is mostly policy and broader placement polish, not the specific `J2` drift problem from the NE5532 output cluster.

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

Current findings:
- The recent output-stage drift fix moved one part of the clutter reduction upstream into placement: output connectors now sit slightly farther outward, which protects the connector-side ground and output attachment geometry before the router runs.
- That small placement bias removed the specific connector-column drift that was forcing the `J2` neighborhood back toward nearby support bodies, while leaving the broader compact-routing heuristics to clean up only the remaining local nets.

#### 3.1.2 Penalize excessive bends and junctions
Status: `IN PROGRESS`
- Add routing cost penalties for:
  - extra bends,
  - unnecessary jogs,
  - long orthogonal detours,
  - hub-and-spoke routing when a short direct route would do,
  - avoidable junction proliferation.

#### 3.1.3 Add a “small analog circuit” routing mode
Status: `DONE`
- For compact analog circuits, prefer:
  - short local direct routes,
  - one or two bends max for local nets,
  - minimal trunk/spine use,
  - minimal labels unless needed.

Current findings:
- `kicad-pcb/src/kicad_pcb/router.py` now exposes `RoutingHeuristicPolicy.enable_small_analog_local_routing`, and the `analog_audio` profile enables it explicitly while the generic/default routing policy and the non-analog profiles leave it disabled.
- The router now compares compact local 3-pin chain routes against their shared-lane or spine alternatives with a small visual-cost model that penalizes trunk junctions, extra bends, and short jog fragments; when the chain is cleaner, analog mode chooses `strategy="chain"` with `heuristic_override="small_analog_local_routing"`.
- Focused regression coverage in `tests/unit/test_phase6_wire_simplification.py` now proves the analog profile prefers a chain on a compact local input-stage fixture where `generic_digital` still keeps the grouped shared-lane plan, and the real NE5532 profile-difference regression in `tests/unit/test_netlist_commands.py` now locks the higher-chain / lower-shared-lane analog summary contract.

---

## 3.2 Add net-class-specific routing preferences

Status: `IN PROGRESS`

### Problem
All nets appear to be treated too generically.

### Required result
Different net types should route differently.

### Tasks

#### 3.2.1 Classify nets
Status: `DONE`
Classify nets into categories such as:
- signal-chain nets,
- feedback nets,
- shunt-to-ground nets,
- power nets,
- connector-only nets,
- local decoupling nets.

Current findings:
- `kicad-pcb/src/kicad_pcb/router.py` now exposes a first-class routing taxonomy through `RouteDecision.classification` and the debug-dump `net_classification` payload instead of collapsing every non-power net to `"signal"`.
- The current taxonomy includes `power`, `local_decoupling`, `shunt_ground`, `connector_only`, `connector_attachment`, `signal_chain`, `feedback`, and `generic_signal`.
- Classification is currently derived from the routed net name plus the participating ref families, which is enough to distinguish the stable analog-audio seams already in use: input/output path nets now classify as `signal_chain`, connector-plus-passive attachment nets classify as `connector_attachment`, and explicit inverting/feedback nets such as `U1A_INV` classify as `feedback`.

#### 3.2.2 Route by net class
Status: `IN PROGRESS`
- **feedback nets**: shortest and most local possible
- **shunt-to-ground nets**: prefer short vertical drop to nearby ground
- **power nets**: clean local rail presentation
- **signal-chain nets**: left-to-right readable path
- **connector nets**: short clean attachment to connector pins

Current findings:
- `kicad-pcb/src/kicad_pcb/router.py` now contains a compact rightward-tail carve-out plus `_compact_vertical_tail_route(...)`, which keeps short asymmetric output tails readable instead of forcing them onto a redundant local ladder trunk.
- The same router module now uses `_compact_local_ground_cluster_route(...)` for tiny output-side `GND` clusters so connector/support ground returns use one calm horizontal lane with body-aware entry points instead of a small centroid knot.
- The formal net taxonomy now directly gates those routing choices: compact tail routing is limited to `signal_chain` and `connector_attachment` nets, while the small analog chain preference is limited to `signal_chain`, `connector_attachment`, and `feedback` nets instead of firing on every compact 3-pin shape.
- Focused regression coverage for both behaviors now lives in `tests/unit/test_phase6_wire_simplification.py`.

#### 3.2.3 Prefer labels only when they improve clarity
Status: `IN PROGRESS`
- Avoid label fallback for short readable local nets.
- Use labels only when they reduce crossing/clutter or improve comprehension.

Current findings:
- The new compact-tail and compact-ground local routes both reduce the need to fall through to noisier fallback behavior in the densest output-side neighborhood, which keeps more of the NE5532 output cluster readable as direct local wiring instead of label-style or over-spined routing.
- `kicad-pcb/src/kicad_pcb/router.py` now lets short 2-pin `signal_chain`, `feedback`, and `connector_attachment` nets stay on a local direct wire even when tier inference would normally force label fallback, using `MAX_DIRECT_WIRE_MM` as the readability cap.
- That override is still intentionally narrow: generic cross-tier nets keep the older label fallback, and the protected Phase 4 connector-to-IC tier-distance case remains label-routed because its geometry stays just beyond the short-local readability cutoff.
- Focused regressions in `tests/unit/test_phase6_wire_simplification.py` now lock all three allowed seams: short `STAGE_L`, `U1A_INV`, and `LEFT_IN` nets remain label-free and route as `strategy="direct"`, while the older Phase 4 tier-distance label tests still define the generic and non-local connector fallback boundary.

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

Current findings:
- The current output-side `GND` refinement is not yet a full generic local-ground drop policy, but it does now keep the `J2.S` / `R5.2` / `R7.2` cluster on a local horizontal lane with a nearby ground symbol instead of routing those pins through a more distant shared centroid.

#### 3.3.2 Keep stage-local grounds stage-local in drawing
Status: `IN PROGRESS`
- Do not over-centralize grounds in the visual layout.
- Preserve clarity over theoretical “single common ground symbol” compactness.

Current findings:
- The compact local ground-cluster route in `kicad-pcb/src/kicad_pcb/router.py` is the first concrete step here: the NE5532 output-side ground cluster now stays visually local to `J2`, `R5`, and `R7` instead of being absorbed into a noisier generic cluster presentation.
- That change materially reduced the real output-neighborhood clutter while preserving local body avoidance.

---

## Phase 4 - Improve page composition

Status: `DONE`

## 4.1 Use the sheet intentionally

Status: `DONE`

### Problem
The circuit occupies only part of the page and does not look composed.

### Required result
The schematic should look intentionally arranged on the page.

### Tasks

#### 4.1.1 Add page-level packing / centering
Status: `DONE`
- After block placement, compute the overall circuit bounding box.
- Recenter and scale spacing so the main circuit occupies a balanced region of the sheet.
- Avoid leaving most of the page empty unless the design is truly tiny.

Current findings:
- `kicad-pcb/src/kicad_pcb/graphviz_layout/snap.py` already contains `_snap_page_balance(...)`, which recenters the signal-path circuit vertically toward the page center using a grid-quantized corrective shift while leaving power-entry refs fixed and preserving relative IC/decoupling placement.
- The pass is wired into the live Graphviz snap pipeline through `_apply_post_layout_snaps(...)`, so managed schematic generation already applies the page-balance correction after the local readability passes.
- Focused coverage in `tests/unit/test_phase8_layout.py` now locks the page-balance metrics, dead-zone behavior, correction factor, and end-to-end headphone-amp composition guardrails.

#### 4.1.2 Respect title block exclusion zone
Status: `DONE`
- Add or improve a keep-out region around the title block.
- Ensure no meaningful circuitry crowds or overlaps that visual area.

Current findings:
- `kicad-pcb/src/kicad_pcb/graphviz_layout/snap.py` already contains `_snap_central_composition(...)`, which enforces a bottom title-block clearance band, keeps the op-amp stage within a central vertical window, and preserves grid alignment while nudging only signal-path refs.
- That same central-composition pass is already part of `_apply_post_layout_snaps(...)`, so the title-block keep-out runs automatically during managed schematic generation rather than existing only as a lint-time check.
- `tests/unit/test_phase8_layout.py` now covers both the helper-level title-block/op-amp constraints and the end-to-end guarantee that the generated headphone-amp schematic stays out of the title-block zone and emits no Phase 8 composition lints.

#### 4.1.3 Keep power block and main circuit visually connected
Status: `DONE`
- Power/decoupling may be above the main path, but it should still read as part of the same design.

Current findings:
- `kicad-pcb/src/kicad_pcb/graphviz_layout/snap.py` now includes `_snap_power_block_cohesion(...)`, a dedicated Phase 8 composition pass that keeps `POWER_ENTRY` refs laterally close to the active/signal anchor after the signal path has been re-centered. The pass prefers decoupling-target ICs when available, otherwise falls back to core refs and then the broader signal cluster.
- The implementation only adjusts x-coordinates of non-`#PWR` power-entry refs, preserving the existing vertical page-balance and title-block behavior while preventing the power block from remaining stranded at the far left edge.
- Focused coverage in `tests/unit/test_phase8_layout.py` now locks both anchor-selection paths: power-entry refs tether to the core/decoupling cluster when one exists, and otherwise fall back to the broader signal cluster.

---

## 4.2 Improve block spacing and alignment

Status: `DONE`

### Problem
The stage boundaries are not strong enough visually.

### Required result
Blocks should align clearly and read left-to-right.

### Tasks

#### 4.2.1 Align major signal-path nodes horizontally
Status: `DONE`
- Input block, stage 1, stage 2, and output block should share a coherent horizontal axis where appropriate.

#### 4.2.2 Use consistent spacing between blocks
Status: `DONE`
- Add spacing rules for:
  - within-block compactness,
  - between-block separation,
  - power-block offset,
  - connector margin from page edge.

Current findings:
- The snap pipeline now enforces more consistent local spacing for stage-edge blocks through `_snap_input_stage_cohesion(...)` and `_snap_output_stage_cohesion(...)` in `kicad-pcb/src/kicad_pcb/graphviz_layout/snap.py`.
- `kicad-pcb/src/kicad_pcb/graphviz_layout/snap.py` now also applies `_snap_major_signal_axis(...)` after central composition, aligning the representative input/core/output spine onto a shared grid-snapped y-axis while intentionally leaving feedback, decoupling, and power-support lanes alone.
- The representative selection is deliberately narrow: prefer the input connector, explicit core device refs, and the output connector when present, falling back to a single non-connector stage representative only when a stage has no connector. This avoids over-correcting misclassified support passives.
- Input-side staging is now explicitly left-bounded and compact, while output-side staging is explicitly right-bounded and compact; both sides can split longer support chains across inner/outer lanes without breaking the short left-to-right transition into and out of the op-amp.
- `kicad-pcb/src/kicad_pcb/graphviz_layout/snap.py` now also applies `_snap_major_block_spacing(...)` after `_snap_major_signal_axis(...)`, treating the input/core/output blocks as ordered groups and shifting later groups together when an adjacent x-gap is either too small or too large.
- The new pass is intentionally block-level rather than component-level: it preserves each block's internal geometry while normalizing adjacent block separation into a bounded range, including passive-only fixtures that do not have an IC anchor.
- Focused coverage in `tests/unit/test_phase4_layout.py` now checks compactness, left/right bounds, intrusion avoidance, and the new longer-chain lane behavior for both stage edges.
- Focused coverage in `tests/unit/test_phase8_layout.py` now checks both the core-anchored and connector-fallback major-axis cases, the new overlarge/undersized adjacent block-gap cases, and an integration guard on the readability fixture's adjacent major-block span gaps.
- Output connectors also now receive one extra snap-step of outward clearance beyond the nominal connector lane, which keeps the right-side attachment geometry readable without widening the whole stage.

#### 4.2.3 Keep local loops compact
Status: `DONE`
- Feedback loop and buffer loop should remain much tighter than the spacing between major functional blocks.

Current findings:
- `kicad-pcb/src/kicad_pcb/graphviz_layout/snap.py` now treats true `BUFFER_STAGE` support parts as members of the compact output-side local loop, so small buffer/output handoff parts stay near the owning op-amp instead of drifting to major stage spacing.
- The implementation explicitly excludes IC refs from that movable support set, which preserves the established second-stage/output composition on the real NE5532 fixture while still compacting the passive loop members.
- Focused coverage in `tests/unit/test_phase4_layout.py` now locks both the existing feedback/output transition behavior and the new compact buffer-loop support behavior, and the real NE5532 command/guardrail regressions continue to protect the output neighborhood composition.

---

## 4.3 Add optional important net labels

Status: `DONE`

### Problem
Internal nodes are harder to inspect than they need to be.

### Required result
Important named nets can be shown when helpful.

### Tasks

#### 4.3.1 Decide label policy
Status: `DONE`
- Add configuration for:
  - minimal labels,
  - debug labels,
  - always-show-important-labels.

Current findings:
- `kicad-pcb/src/kicad_pcb/router.py` now exposes an explicit bundled label-policy registry with the three requested modes: `minimal`, `debug`, and `always-show-important-labels`.
- `kicad-pcb/src/kicad_pcb/cli.py` now surfaces `--label-mode` on both `apply-netlist` and `new-from-netlist`, and the selected mode is threaded through `kicad-pcb/src/kicad_pcb/commands/_sch_apply.py`, `kicad-pcb/src/kicad_pcb/commands/netlist.py`, `kicad-pcb/src/kicad_pcb/results.py`, and `kicad-pcb/src/kicad_pcb/formatting.py`.
- The debug sidecar now records `label_mode_name`, and focused coverage in `tests/unit/test_phase4_layout.py`, `tests/unit/test_phase7_ux.py`, `tests/unit/test_presentation.py`, and `tests/unit/test_netlist_commands.py` locks the resolver, CLI wiring, presentation output, and mode-specific routing behavior.

#### 4.3.2 Identify important nets for display
Status: `DONE`
For this example, consider exposing:
- `LEFT_IN`
- `IN_L_AC`
- `VOL_L_OUT`
- `OUT_L_STAGE1`
- `BUF_L_IN`
- `HP_L_OUT`

Current findings:
- `kicad-pcb/src/kicad_pcb/router.py` now identifies important display nets from explicit stage-seam structure when `BlockLayout` data is available, instead of relying on the earlier broad name-only fallback. The promoted seams now cover input entry (`LEFT_IN`), input-to-preconditioning handoff (`IN_L_AC`), preconditioning-to-op-amp handoff (`VOL_L_OUT`), op-amp-to-interstage handoff (`OUT_L_STAGE1`), interstage return into the second stage (`BUF_L_IN`), and final output-to-connector handoff (`HP_L_OUT`).
- The same logic now explicitly excludes feedback-internal and raw/post-series internal nets such as `U1A_INV`, `OUT_L_STAGE2_RAW`, and `AFTER_R6` from important-label promotion.
- Focused coverage in `tests/unit/test_phase4_layout.py` locks the seam selection logic on a synthetic left-channel slice, and `tests/unit/test_netlist_commands.py` now proves the real NE5532 fixture surfaces the expected visible labels when run with `--label-mode always-show-important-labels`.

#### 4.3.3 Avoid label overuse
Status: `DONE`
- Only place labels where they improve reading or debugging.
- Do not replace good local wiring with gratuitous labels.

Current findings:
- `kicad-pcb/src/kicad_pcb/router.py` no longer promotes an extra visible label for `always-show-important-labels` when a net already routes as a short direct 2-pin wire. This keeps trivial local seams readable as wiring first instead of annotating them redundantly.
- Important-mode promotion still applies on the explicit multi-pin stage seams that benefit from inspection labels, so the real NE5532 fixture continues to surface `LEFT_IN`, `IN_L_AC`, `VOL_L_OUT`, `OUT_L_STAGE1`, `BUF_L_IN`, and `HP_L_OUT` without reintroducing labels on already-obvious local direct routes.
- Focused coverage in `tests/unit/test_phase4_layout.py` now locks both sides of that boundary: direct important seams stay label-free, while multi-pin stage seams still get one visible label in important mode.

---

## Phase 5 - Add validation, tests, and regression protection

Status: `DONE`

## 5.1 Add schematic-readability regression fixtures

Status: `DONE`

### Required result
The quality improvements should stay fixed.

### Tasks

#### 5.1.1 Add this amplifier as a named fixture
Status: `DONE`
- Store the amplifier example as a durable regression fixture.

Current findings:
- The canonical NE5532 left-channel readability artifact is now treated as a named fixture, `ne5532_headphone_amp_left_current`, with its checked-in IR, baseline schematic, metrics, and README all living under `tests/fixtures/readability/ne5532_headphone_amp_left_current/`.
- `tests/__init__.py` now provides the shared fixture registry used by fixture-oriented tests and the readability review utility, so the current and regressed NE5532 fixtures are resolved by name instead of repeated raw paths.
- Focused coverage in `tests/unit/test_readability_review_script.py` now locks the review script defaults to that named fixture registry, which makes the fixture durable as a reusable regression target rather than just a directory convention.

#### 5.1.2 Add expected structural assertions
Status: `DONE`
Assert that:
- there are two distinct drawable op-amp units for `U1`,
- decoupling is associated with the op-amp region,
- output chain members are ordered logically,
- feedback parts are near their op-amp stage,
- unused connector pins are handled explicitly.

Current findings:
- Phase 7 guardrails in `tests/unit/test_phase7_regression_guardrails.py` now go beyond aggregate column/separation metrics and include a concrete NE5532 interstage/output neighborhood assertion.
- That guardrail anchors itself to the rightmost placed `U1*` unit and locks in the local output-side composition: `C6`, `R5`, `C7`, `R6`, `R7`, and `J2` stay on the output side of the second stage, `R5` remains between the `C6` handoff and `R6`, and the final `R7` / `J2` tail stays farther outward than the handoff pair.
- `tests/unit/test_netlist_commands.py` now treats the real NE5532 source netlist as a shared named fixture and locks the remaining structure-specific guarantees that the readability fixture could not express: generation must split `U1` into drawable `U1A` / `U1B` / `U1P` units, keep `C1`-`C4` closer to the op-amp region than to the audio connectors, keep feedback parts `R2` / `R3` local to `U1A`, and still emit explicit no-connect markers for the unused TRS ring pins.

#### 5.1.3 Add route-quality metrics
Status: `DONE`
Track and compare:
- wire count,
- bend count,
- junction count,
- average local-net length,
- max feedback-loop span.

Do not overfit to exact numbers, but enforce sane upper bounds.

Current findings:
- Phase 7 guardrails in `tests/unit/test_phase7_regression_guardrails.py` now include a concrete output-neighborhood routing check for the real NE5532 fixture, not only whole-page wire-stub and lint metrics.
- The new guardrail measures the local wire box around `C6`, `R5`, `R6`, `C7`, `R7`, and `J2`, and asserts that the generated schematic stays below the current small-jog threshold (`<= 12` short local segments and `<= 0.35` local short-segment ratio) while also remaining materially better than the captured regressed snapshot for the same neighborhood.
- With the currently landed placement and routing work, that concrete output box now measures `32` total segments / `7` short segments (ratio `0.219`) in internal generation mode for `code_review/ne5532_headphone_amp_netlist.json`.
- Current route-level regressions in `tests/unit/test_phase6_wire_simplification.py` now lock in the compact output-tail carve-outs and the compact output-side `GND` lane so those local improvements do not silently drift.
- `tests/unit/test_netlist_commands.py` now adds a second real-fixture route-quality guardrail on the generated NE5532 schematic itself: total wire count, orthogonal bend count, junction count, average stage-local net span (`LEFT_IN`, `IN_L_AC`, `BUF_L_IN`, `U1A_INV`, `AFTER_R6`, `HP_L_OUT`), and the `U1A_INV` feedback-loop span all stay under tolerant upper bounds instead of drifting back toward sprawling routing.

---

## 5.2 Add topology warnings / linting

Status: `DONE`

### Required result
The tool should help catch suspicious circuits before drawing them prettily.

### Tasks

#### 5.2.1 Add analog lint rules
Status: `DONE`
Warn on:
- coupling capacitor directly paralleled by resistor,
- missing op-amp feedback,
- output capacitor followed by no defined load/bleed path,
- unconnected connector pins without explicit no-connect,
- decoupling parts far from active device in layout phase,
- likely mistaken stage topology.

Current findings:
- The first analog warning family is already live in the advisory-warning path: `kicad-pcb/src/kicad_pcb/commands/_validate.py` emits `INPUT_COUPLING_BYPASSED_BY_RESISTOR` when a capacitor and resistor bridge the same two nets and one of those nets reads as an input-path net.
- That rule covers the long-running `R1` / `C5` concern in the NE5532 review fixture and is regression-covered at both layers:
  - helper-layer coverage in `tests/unit/test_sch_apply.py`
  - command-layer coverage in `tests/unit/test_netlist_commands.py`
- The real `code_review/ne5532_headphone_amp_netlist.json` fixture is already pinned to warn with `INPUT_COUPLING_BYPASSED_BY_RESISTOR` for bridge refs `C5` and `R1` between `LEFT_IN` and `IN_L_AC`.
- The next missing output-side family is now live too: `kicad-pcb/src/kicad_pcb/commands/_validate.py` emits `OUTPUT_CAP_NO_DEFINED_LOAD_OR_BLEED` when an op-amp output is AC-coupled onto an output-like/connector net without a resistor-defined bleed or load path to a rail/reference net on the output side.
- That output-side warning is regression-covered at both layers as well:
  - helper-layer coverage in `tests/unit/test_sch_apply.py`
  - command-layer coverage in `tests/unit/test_netlist_commands.py`
- The next topology family is now live too: `kicad-pcb/src/kicad_pcb/commands/_validate.py` emits `OPAMP_STAGE_TOPOLOGY_LIKELY_MISTAKEN` when a stage looks non-inverting at the signal input but the inverting-input node only has local feedback resistor(s) and lacks any resistor-defined shunt/reference path.
- That stage-topology warning is also regression-covered at both layers:
  - helper-layer coverage in `tests/unit/test_sch_apply.py`
  - command-layer coverage in `tests/unit/test_netlist_commands.py`
- The remaining layout-side family has now started too: `kicad-pcb/src/kicad_pcb/commands/_sch_apply.py` emits `DECOUPLING_FAR_FROM_ACTIVE_DEVICE` after placement when a capacitor bridging a rail net to ground is positioned too far from the nearest active device that shares that rail and still carries non-power signal nets.
- This decoupling warning is intentionally apply/layout scoped rather than netlist-only scoped, so it currently surfaces through `apply-netlist` and `new-from-netlist` where final symbol coordinates exist.
- The matcher is now tighter about support polarity too: positive-rail decouplers prefer candidate active devices below the capacitor, while negative-rail decouplers prefer candidate active devices above it, which reduces broad shared-rail matches when multiple devices sit on the same supply net; the underlying rail alias vocabulary and polarity classifier now live in `kicad-pcb/src/kicad_pcb/component_types.py`, so generic `is_power_net(...)` checks and the layout-side decoupling matcher share the same alias set (`AVCC`, `AVDD`, `DVDD`, `VPOS`, `VAA`, `VS+`, `AVEE`, `DVEE`, `VNEG`, `VBB`, `VS-`) instead of diverging.
- The same shared `power_rail_polarity(...)` helper now also drives the remaining rail-aware consumers that previously carried their own alias lists: `kicad-pcb/src/kicad_pcb/commands/_validate.py`, `kicad-pcb/src/kicad_pcb/tier.py`, `kicad-pcb/src/kicad_pcb/block_detection.py`, `kicad-pcb/src/kicad_pcb/router.py`, and the power-net skip helpers in `kicad-pcb/src/kicad_pcb/lint/sch.py`.
- A second sweep also removed the remaining ground-only alias duplication by adding shared `is_ground_like_name(...)` support in `kicad-pcb/src/kicad_pcb/component_types.py`; the `#PWR` / `#FLG` row-placement snap in `kicad-pcb/src/kicad_pcb/graphviz_layout/snap.py` and the ground-family classification logic in `kicad-pcb/src/kicad_pcb/block_detection.py` now both use the same GND/VSS/0V/AGND-family predicate instead of carrying local lists.
- Focused regression coverage for the layout-side family now lives in `tests/unit/test_netlist_commands.py`, including the far-placement warning case, the nearby decoupler guard case, explicit positive- and negative-rail side-selection regressions, and propagation through `new-from-netlist`.
- Additional warning families in the same advisory path now also cover missing/nonlocal op-amp feedback, output floating, output shorted to a rail, output coupling bypassed by resistor, and ambiguous unused connector pins, so Phase 5.2.1 is underway rather than untouched.
- Audit confirmation: the live warning pipeline now covers every family listed in 5.2.1 across the advisory-validation path (`INPUT_COUPLING_BYPASSED_BY_RESISTOR`, `OPAMP_FEEDBACK_MISSING_OR_NONLOCAL`, `OUTPUT_CAP_NO_DEFINED_LOAD_OR_BLEED`, `CONNECTOR_UNUSED_PINS_AMBIGUOUS`, `OPAMP_STAGE_TOPOLOGY_LIKELY_MISTAKEN`) plus the layout/apply path (`DECOUPLING_FAR_FROM_ACTIVE_DEVICE`), with focused helper-layer and command-layer regression coverage already present.

#### 5.2.2 Add warning surfacing
Status: `DONE`
- Ensure warnings can appear in:
  - CLI output,
  - logs,
  - generated metadata,
  - optional sidecar report.

Current findings:
- `kicad-pcb/src/kicad_pcb/commands/_sch_apply.py` now writes a deterministic sidecar report `OpenClaw_Warnings.json` into the generated project directory for non-dry-run `apply-netlist` and `new-from-netlist` flows.
- The sidecar captures the same advisory warning payloads already returned in structured command results, along with generation context such as project paths, input netlist path, validation mode, symbol directories used, managed-item counts, and warning count.
- `ApplyNetlistResult` and `NewFromNetlistResult` now surface `warning_report_path`, and `kicad-pcb/src/kicad_pcb/formatting.py` prints that path in the human-readable CLI output so users can discover the persisted warning report without switching to JSON mode.
- Focused command and presentation coverage now asserts that the sidecar is written and that persisted warning codes match the structured warning set.

---

## 5.3 Add before/after comparison tooling

Status: `DONE`

### Required result
Developers should be able to see whether layout quality actually improved.

### Tasks

#### 5.3.1 Save render snapshots during tests or dev mode
Status: `DONE`
- Generate PNG or equivalent preview renders for:
  - current baseline,
  - improved output.

Current findings:
- `scripts/review_schematic_readability.py` now emits comparison preview snapshots into the review bundle using the existing KiCad schematic SVG export path when it works, with optional PNG conversion when `cairosvg` is available.
- Because `kicad-cli sch export svg` on this machine can report success without leaving an SVG behind, the review bundle now falls back to an internal schematic SVG renderer so the standalone bundle still contains real baseline/improved preview files.
- Focused coverage in `tests/unit/test_readability_review_script.py` now verifies both the normal preview-export path and the internal preview renderer path, so `review_report.json` always carries direct visual before/after artifacts alongside the metric drift data.

#### 5.3.2 Add a schematic-quality review script
Status: `DONE`
- Create a simple developer utility that:
  - generates the fixture,
  - reports warnings,
  - prints key layout metrics,
  - saves the result to a known output folder.

Current findings:
- Added `scripts/review_schematic_readability.py`, a repo-local developer utility that regenerates the canonical NE5532 readability fixture in internal mode, captures both `validate-netlist` and `new-from-netlist` warning sets, computes the current readability metrics, compares them against the checked-in current and regressed baselines, and writes a deterministic review bundle under `code_review/generated/` by default.
- The review bundle now includes copied generated root/managed schematics plus `review_report.json` and `review_summary.txt`, which gives a repeatable way to inspect warning drift and readability-metric drift outside the pytest output.
- Added focused regression coverage in `tests/unit/test_readability_review_script.py` proving the script writes the bundle and preserves the expected real-fixture warning and metrics content.

---

## Phase 6 - Optional but strongly recommended cleanup

Status: `DONE`

## 6.1 Separate generic graph heuristics from analog-specific drafting heuristics
Status: `DONE`
- Refactor so generic placement logic is not tangled with analog-special-case logic.
- Keep analog rules in a clear module or strategy layer.

Current findings:
- `RoutingHeuristicPolicy` now isolates the analog-only compact output-tail and compact local-ground-cluster decisions from the generic router flow.
- `LayoutHeuristicPolicy` now isolates the analog-only decoupling, op-amp locality, input-stage cohesion, and output-stage cohesion passes from the generic post-layout snap pipeline.

## 6.2 Add schematic-style profiles
Status: `DONE`
- Add output profiles such as:
  - generic digital,
  - analog audio,
  - power supply,
  - dense debug.
- Use analog-audio profile for this circuit.

Current findings:
- `SchematicHeuristicProfile` now bundles layout and routing heuristic policies under named profiles, and the current registry includes `analog_audio`, `generic_digital`, `power_supply`, and `dense_debug`.
- `apply-netlist` and `new-from-netlist` now accept `--heuristic-profile <name>` and resolve the selected name through `SCHEMATIC_HEURISTIC_PROFILES`, so profile selection is wired through the request path instead of staying as an internal default only.
- Human-readable command output for both `apply-netlist` and `new-from-netlist` now prints `Heuristic profile: <name>`, which makes the active profile visible during normal CLI use without inspecting JSON output.
- Focused behavioral regression coverage now proves the named profiles are not just plumbing: fixture-level tests in `tests/unit/test_phase4_layout.py` and `tests/unit/test_phase6_wire_simplification.py` show `analog_audio`, `generic_digital`, and `power_supply` produce intentionally different layout and routing outcomes on the same local fixtures.

## 6.3 Improve internal debug introspection
Status: `DONE`
- Add optional debug dumps for:
  - block classification,
  - unit splitting,
  - net classification,
  - placement constraints,
  - final route choices.

Current findings:
- `GraphvizLayoutEngine` debug dumps now include the active `heuristic_profile_name`, the active layout heuristic toggles, and a structured `placement_constraints` summary alongside the existing block-layout and coordinate artifacts.
- `apply-netlist` and `new-from-netlist` now accept `--debug-dump <path>` and write a merged JSON sidecar that includes `heuristic_profile_name`, unit-splitting summaries, per-net classification, final route choices, and the active routing-heuristic toggles.
- Focused regression coverage now exists for both the layout-only debug dump and the end-to-end schematic debug sidecar.
- Command-layer debug-dump regressions in `tests/unit/test_netlist_commands.py` now prove the same input circuit yields different serialized `final_route_choices` and heuristic overrides when `apply-netlist` runs with `analog_audio` versus `generic_digital`, and with `power_supply` versus `generic_digital` for the local `GND` cluster path.

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
