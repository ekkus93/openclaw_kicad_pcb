# KiCad Schematic Electrical Invariance and AI Visual Refinement — Implementation Notes

Date: 2026-08-10
Branch: `webapp`
Specification: `docs/KICAD_SCHEMATIC_ELECTRICAL_INVARIANCE_AI_VISUAL_REFINEMENT_SPEC_2026-08-10.md`
TODO: `docs/KICAD_SCHEMATIC_ELECTRICAL_INVARIANCE_AI_VISUAL_REFINEMENT_TODO_2026-08-10.md`
Planning baseline before spec: `d72ff407460896839893d2221cfb5720013bb9e2`
Specification SHA: `ba76720bbcd2a0fe884dc3a035bc525150bf25ea`
Implementation-start SHA: `735cf3c6957f59eded02bc741b69958b34a89b84`

## 1. Baseline acceptance

The exact implementation-start SHA `735cf3c6957f59eded02bc741b69958b34a89b84` passed permanent CI in run `31457972428`:

- Frontend lint, unit tests and production build — job `93675631621` — success.
- Python lint, types, unit and web tests — job `93675631624` — success.
- KiCad integration tests — job `93677917223` — success.
- Browser smoke tests — success.
- Package smoke — success.

This SHA is the immutable implementation-start comparison point for the batch.

## 2. Phase 0 architecture inventory

### 2.1 Electrical input validation

Existing reusable input-side validation remains in:

- `src/kicad_pcb/commands/_validate.py`
- `src/kicad_pcb/commands/_validate_connectivity.py`
- `src/kicad_pcb/ir/validate.py`

The current three-layer IR validation is schema -> semantic -> symbol/pin validation. Generic connectivity lint additionally reports floating components and suspicious single-pin nets. These checks validate the intended Circuit IR but do not prove a post-edit `.kicad_sch` still implements it.

### 2.2 Existing corpus electrical equivalence

`src/kicad_pcb/evaluation/electrical.py` already compares canonical Circuit IR component refs, symbols, values, net names and `(ref, pin, unit)` net membership. `src/kicad_pcb/evaluation/_reports_fixture.py` already exports a generated `.kicad_sch` through `kicad-cli`, parses the KiCad XML netlist, converts it to Circuit IR and invokes electrical equivalence.

Decision: extract/reuse this equivalence logic through a production-safe core. Corpus evaluation must consume the same core rather than retain an independent copy.

### 2.3 KiCad tool and rendering seams

`KicadCliAdapter` already provides injectable wrappers for:

- schematic ERC;
- KiCad XML netlist export;
- schematic SVG export.

`src/kicad_pcb/commands/preview.py` already uses schematic SVG export and optionally converts SVG to PNG through `cairosvg` when available.

Decision: refinement will reuse the adapter and renderer seam. Apply/refine will fail closed when required KiCad verification/export cannot run. Vision review will require an actual image artifact; there is no text-only visual fallback.

### 2.4 Schematic parser/editing seam

`SchematicDoc` is an AST-based `.kicad_sch` wrapper. Existing emitters support symbols, wires, labels, global labels, junctions, no-connect markers and power symbols. Existing symbol metadata extraction includes reference, symbol id, value, position, rotation, unit and UUID.

Decision: deterministic refinement operations will mutate typed AST nodes through repository code. Model output is never accepted as raw S-expression text, paths, shell commands or executable code.

### 2.5 Footprints

Circuit IR already has `ComponentIR.footprint`. KiCad XML netlist components also expose footprint data. The current `SchematicDoc.list_symbols()` metadata does not expose the placed symbol Footprint property.

Decision: add explicit schematic component-detail extraction including Footprint, and use the accepted schematic as the immutable footprint baseline when authoritative IR omits a footprint.

### 2.6 No-connect state

KiCad no-connect markers are explicit `(no_connect (at ...))` schematic AST nodes. KiCad XML netlist export does not provide enough information to distinguish an intentionally no-connected pin from an otherwise unconnected pin.

Decision: no-connect invariance is a complementary schematic-parser check, not an XML-netlist-only check. The implementation must map no-connect coordinates to exact placed symbol terminals; ambiguous/unresolvable mapping is a hard failure in production apply/refine mode.

### 2.7 Complete pin inventory and hidden pins

KiCad XML netlist describes electrical connections, not a complete logical pin inventory. Complete pins are available from resolved symbol definitions/library data.

Decision: the authoritative Circuit IR plus symbol index remains the source for expected logical terminal identities; actual net membership comes from KiCad export. Hidden/unconnected terminal verification must not be inferred from absence alone when explicit no-connect semantics matter.

### 2.8 Multi-unit symbols

Existing electrical comparison contains narrowly scoped normalization for generated split refs such as `U1A` back to logical `U1`, conditioned on source refs/unit membership.

Decision: preserve that behavior in the extracted core and add regression tests that prevent suffix stripping from aliasing ordinary references or unrelated components.

### 2.9 Named and unnamed nets

Existing comparison preserves safe sheet-scoped-name normalization but otherwise compares names literally.

Decision: preserve exact identity for declared/named nets. For narrowly recognized KiCad autogenerated/unnamed nets, compare canonical terminal partitions instead of unstable generated names. Do not broadly classify arbitrary user net names as unnamed.

### 2.10 Helper/power symbols

Power symbols are graphical/helper schematic symbols and can appear outside the authoritative logical component inventory while contributing connectivity.

Decision: helper classification must be explicit and based on concrete KiCad semantics/properties (for example `in_bom=no`/`on_board=no` plus supported power-symbol identity), never loose prefix suppression that could hide a normal unexpected component.

### 2.11 Existing deterministic quality infrastructure

Reuse rather than duplicate:

- `src/kicad_pcb/schematic_metrics.py`
- `src/kicad_pcb/_schematic_metrics_*.py`
- `src/kicad_pcb/lint/_sch_*.py`
- `src/kicad_pcb/corpus/layout_features.py`
- `src/kicad_pcb/evaluation/scoring.py`
- `src/kicad_pcb/evaluation/similarity.py`

New refinement metrics will normalize existing raw measurements and add only missing hard validity/readability measurements.

### 2.12 LLM provider boundary

The webapp has an explicit `LlmProviderCapabilities` contract and provider-specific OpenAI-compatible/Ollama payload builders. Current normalized messages are text-only.

Decision: add explicit image-input capability/protocol declarations. No model-name substring heuristic may infer vision. A vision-required request on a non-vision capability must fail before provider invocation. Existing retry, terminal-outcome, redaction and structured-output hardening contracts remain unchanged.

## 3. Implementation order

The TODO hard-gate order remains authoritative:

1. electrical equivalence/fingerprinting and actual-artifact verifier;
2. isolated transactional candidate lifecycle;
3. registered low-risk operations;
4. deterministic quality metrics and reproducible rendering;
5. explicit vision capability, critic and planner;
6. analyze/plan/apply-once orchestration;
7. wire geometry only after component-only safety is proven;
8. bounded iterative refine, evidence and corpus evaluation;
9. unsafe-fallback/silent-failure audit and exact-SHA closure.

No model-directed mutation is enabled before the Phase A and Phase B hard gates are green.
