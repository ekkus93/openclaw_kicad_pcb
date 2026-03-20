# SCHEMATIC_FIXES.md

## Purpose

This document provides background and implementation context for improving the OpenClaw KiCad schematic-generation skill. It accompanies `SCHEMATIC_FIXES_TODO1.md`.

The immediate target is the generated schematic for the op-amp headphone amplifier derived from:
- `OpAmp_Audio_Amp_notes.txt`
- `opamp_audio_headphone_amp_left_netlist.json`

The current output is materially better than earlier versions, but it still has correctness and readability issues that need to be addressed in code, not by hand-editing the KiCad file.

---

## Summary of the current state

The current OpenClaw pipeline appears to be doing real schematic generation work now, not merely dumping symbols randomly.

At a high level, the current generator appears to:

1. rebuild the managed schematic from a netlist-like internal representation,
2. use a Graphviz-based or graph-based placement stage,
3. compute pin endpoints from symbol definitions and transforms,
4. route nets using orthogonal/direct/spine-like heuristics,
5. emit a managed `.kicad_sch` that is referenced by the top-level schematic.

This is a meaningful improvement over older behavior. The generated result shows:
- a recognizable left-to-right signal path,
- separation of supply decoupling from the main audio path,
- placement that is no longer collapsed into a single pile,
- and evidence of an actual layout/routing pipeline.

However, the output is still not good enough for an analog audio schematic.

---

## Main findings from review

## 1. The biggest architectural issue is multi-unit op-amp handling

The design intent uses both halves of an `NE5532`:
- `U1A` as the first gain stage,
- `U1B` as the buffer/output stage.

The generator is not yet expressing that clearly as separate drawable units.

That means the tool is still too close to a model where one reference designator is treated as one drawable symbol body. That is not sufficient for dual op-amps, quad op-amps, multi-gate logic parts, and other multi-unit devices.

This is the most important structural issue in the generator.

### What the generator must understand
It must distinguish between:
- **device**: the real component, such as `U1`
- **placed unit**: the schematic-visible sub-part, such as `U1A` and `U1B`

Without that distinction:
- placement is wrong,
- feedback topology is visually unclear,
- routing may be ambiguous,
- and the KiCad emission will not match how humans expect the circuit to be drawn.

---

## 2. The biggest electrical concern is the `R1` / `C5` topology

From the provided netlist, `R1` appears to be in parallel with `C5`.

That means the intended input coupling capacitor is effectively bypassed by a 100k resistor across the same two nodes.

This may be a genuine design intention, but it is suspicious enough that the tool should not simply ignore it.

### Possible explanations
- the netlist derived from the notes is wrong,
- the notes were ambiguous and got interpreted badly,
- or the design itself needs review.

### What the tool should do
At minimum, the system should have enough topology awareness to detect and warn about suspicious analog constructs like:
- a coupling capacitor directly paralleled by a resistor,
- a missing or non-local feedback loop,
- ambiguous connector pin usage,
- or an output-coupling stage that does not visually read correctly.

The system should either:
- fix netlist extraction if the intent is clear,
- or preserve the extracted netlist faithfully but emit a warning.

---

## 3. The output is still governed too much by generic graph layout and not enough by analog drafting intent

The current schematic is better, but it still looks like:
- “components arranged in rough signal-flow order”
rather than:
- “an intentionally drafted analog audio schematic”

For analog circuits, some relationships must dominate the layout:

- feedback parts should hug the op-amp,
- gain-to-ground resistor should hang from the inverting input node,
- coupling capacitor between stages should sit directly between those stages,
- output resistor + capacitor + bleed resistor + jack should read as one output chain,
- decoupling should visually belong to the IC it supports,
- grounded shunt elements should drop locally to ground.

These are not minor style preferences. They are core readability rules for analog schematics.

---

## 4. Stage partitioning needs to become explicit

This circuit naturally decomposes into functional blocks:

1. input connector and AC coupling
2. volume control / bias network
3. first op-amp gain stage
4. interstage coupling / bias
5. second op-amp buffer stage
6. output conditioning and output jack
7. supply decoupling

The current generator is only partially expressing those blocks.

To make the output look competent, the block structure should become explicit in the placement model.

---

## 5. Decoupling placement is still wrong for schematic readability

The supply decoupling capacitors are too far from the op-amp in the drawing.

Even though this is schematic capture and not PCB placement, human readers still expect local decoupling to be drawn close to the active device. The current output makes the decoupling look like a separate mini-circuit rather than part of the op-amp support network.

The generator should explicitly associate decouplers with their target IC and place them accordingly.

---

## 6. Routing complexity is still too high for such a small circuit

This is a small analog amplifier. It should not need visually busy routing.

The current result has too many:
- jogs,
- junctions,
- long orthogonal detours,
- and routing structures that feel like a generic graph drawing solution.

The right fix is not only a smarter router. It is also stronger placement.

A good placement should make most local analog nets trivial to route.

---

## 7. Connector handling is not explicit enough

This is a left-channel-only schematic using TRS-style connectors.

That creates a documentation/readability problem:
- are unused ring pins intentionally unused,
- or were they forgotten,
- or is the drawing incomplete?

The generator should make that explicit by either:
- using a simpler connector symbol,
- or marking unused pins with no-connect markers,
- or using a clearly intentional left-only representation.

---

## 8. Page composition is still weak

The page still contains a lot of dead space, and the circuit does not look intentionally composed.

The main signal chain should occupy the page in a balanced way, with:
- input at left,
- amplification stages in the middle,
- output at right,
- power/decoupling attached above or around the active devices,
- and no awkward crowding near the title block.

This suggests the tool needs a page-composition pass after initial block placement.

---

## Desired target appearance

The improved output should read roughly like this:

- **Left side**
  - input jack
  - input coupling capacitor
  - resistor / pot network for input loading and volume

- **Center-left**
  - `U1A` non-inverting amplifier
  - local feedback resistor from output to inverting input
  - local resistor from inverting node to ground

- **Center**
  - stage-1 output to interstage coupling capacitor
  - bias resistor for the next stage

- **Center-right**
  - `U1B` configured as a unity-gain buffer
  - clear short feedback from output to inverting input

- **Right side**
  - output resistor
  - output coupling capacitor
  - output node with bleed resistor to ground
  - output jack

- **Above / around op-amps**
  - rail symbols and local decoupling capacitors placed as op-amp support circuitry, not as a detached block

This should look like a human intentionally drafted it, even if it was generated automatically.

---

## Design/implementation philosophy

## 1. Preserve electrical truth first
The generator must not “beautify” the circuit by changing real connectivity silently.

If the extracted netlist is suspicious:
- preserve it,
- warn about it,
- and improve the upstream extraction if possible.

Do not hide electrical issues behind pretty layout.

## 2. Distinguish structural modeling from visual drafting
There should be a clean separation between:
- the circuit graph / netlist truth,
- the symbol/device/unit model,
- block inference,
- visual placement constraints,
- and routing heuristics.

This is important so that future support for:
- quad op-amps,
- logic gates,
- hierarchical blocks,
- power supplies,
- and digital interfaces
does not become tangled.

## 3. Prefer deterministic heuristics over vague magic
For analog drafting, strong explicit heuristics are better than weak emergent behavior.

Examples:
- “feedback resistor should be within N grid units of its op-amp output and inverting input”
- “shunt-to-ground resistor should prefer vertical downward placement to local ground”
- “decoupling capacitor should be assigned to nearest active device supply pin”
- “output connector should be placed to the right of the final output node”

These are concrete and testable.

## 4. Placement should solve most of the problem
The router should not have to rescue a bad placement.

For small analog schematics, the right strategy is:
- classify the blocks,
- place them intelligently,
- then keep routing simple.

---

## Recommended implementation themes

### Theme A - device/unit model
This is mandatory. Without it, dual op-amp output will keep looking wrong.

### Theme B - analog block inference
The tool needs to recognize:
- feedback structures,
- coupling stages,
- output chains,
- decoupling clusters.

### Theme C - net-class-aware routing
Feedback nets, local grounds, rails, and signal-chain nets should not all be treated the same.

### Theme D - topology linting
The generator should help catch suspicious analog mistakes such as the current `R1/C5` case.

### Theme E - page composition
A final packing / composition pass is needed so the output uses the sheet intentionally.

---

## Specific issues that should be fixed for this amplifier example

1. Represent `NE5532` as separate stage-visible units.
2. Make stage 1 feedback visually local and obvious.
3. Make stage 2 follower feedback visually local and obvious.
4. Place decoupling near the op-amp.
5. Make the output chain read clearly as:
   - op-amp output -> resistor -> capacitor -> output node/jack
6. Make grounded shunt parts visually drop to local ground.
7. Resolve or warn on the suspicious `R1/C5` topology.
8. Make connector usage explicit, especially unused pins.
9. Reduce wire/junction clutter.
10. Improve use of the sheet and avoid title-block crowding.

---

## What success looks like

Success is not merely that KiCad opens the file.

Success means:
- a human can understand the topology quickly,
- the schematic visually matches analog drafting norms,
- multi-unit devices are represented correctly,
- suspicious topologies are surfaced,
- and the result is stable under regeneration.

---

## Suggested deliverables from implementation

Copilot should aim to produce:

1. code changes to the schematic-generation pipeline,
2. new or updated fixture tests for the amplifier example,
3. topology-warning support,
4. improved generated `.kicad_sch` output for this example,
5. optional render snapshots for before/after comparison,
6. comments or docs clarifying the device-vs-unit model.

---

## Final note for implementation

Do not treat this as a pure routing problem.

The root issues are:
- internal representation,
- analog-aware block inference,
- placement constraints,
- and only then routing.

If placement is fixed correctly, the routing layer can stay comparatively simple for this class of schematic.
