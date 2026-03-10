"""ORIENTATION_CONVENTIONS.md — Phase 9.1 Reference.

# Component Orientation Conventions (Phase 9.1)

This document defines the orientation conventions for KiCad schematic components
to improve readability and visual clarity. These conventions ensure that component
orientations support both function and reading flow, rather than just fitting routing.

## Guiding Principles

1. **Readability First**: Orient components to match how humans read circuits.
2. **Functional Clarity**: Orientation should reflect the component's role in the circuit.
3. **Consistency**: Similar components in similar roles should have consistent orientations.
4. **Signal Flow**: Support left-to-right signal flow through the main signal path.
5. **Power Visualization**: Show vertical power-to-ground connections clearly.


## Orientation Conventions by Component Type

### Connectors (J, CON, P, SJ, TJ)

**Principle**: Connectors should face inward toward the circuit, not outward toward page edges.

**Rules**:
- **Input connectors** (tier 0, or assigned "input" role): **0°** (face right)
  - Pins point toward the circuit interior
  - Supports left-to-right signal entry
  
- **Output connectors** (maximum tier, or assigned "output" role): **180°** (face left)
  - Pins point back toward the circuit
  - Shows signal flowing out from the circuit
  
- **Intermediate connectors**: **0°** by default
  - When tier-based logic applies, intermediate tiers default to 0°
  - Role-based assignment overrides tier-based logic

**Examples**:
- Audio input jack: 0° (pins face right, signal enters from left)
- Audio output jack: 180° (pins face left, signal exits to right)
- Power supply connector: determined by tier or explicit role


### Op-Amps / ICs (U, IC, OA)

**Principle**: Maintain stable, predictable orientation across all schematics.

**Rule**: Always **0°** (standard KiCad op-amp symbol orientation)

**Benefits**:
- Inputs are on the left, output is on the right
- Non-inverting input (pin 3) typically at top
- Inverting input (pin 2) typically at bottom
- Enables rapid pattern recognition across multiple circuits
- Consistent with KiCad standard symbol orientation

**Rationale**:
Consistent IC orientation allows readers to quickly understand:
- Signal entry points (left side)
- Signal exit point (right side)
- Power/ground pins (typically top/bottom)
- Feedback connections (output to inverting input)


### Passive Components — Series (R, C, L with both pins on signal nets)

**Principle**: Align with predominant signal-flow direction.

**Rule**: Prefer **0°** (horizontal) in left-to-right layout
- Supported by the position heuristic: when x-spread to neighbors dominates
  (sum of |Δx| > sum of |Δy|), default to horizontal (0°)
- Supports left-to-right signal flow
- Visually shows progression through the circuit

**Examples**:
- Input coupling capacitor: 0°
- Series resistor in signal path: 0°
- Output coupling capacitor: 0°

**Fallback**:
- If sum of vertical distances (|Δy|) to signal-net neighbors exceeds sum
  of horizontal distances (|Δx|), rotate to **90°** (vertical)
- This handles in-column feedback or coupling components


### Passive Components — Shunt (R, C with one power pin, one signal pin)

**Principle**: Show vertical connection from signal path to power/ground rail.

**Rule**: Always **90°** (vertical)
- One pin connects to signal net
- One pin connects to power/ground net
- Vertical orientation visually demonstrates the power-to-signal connection

**Examples**:
- Bypass capacitor (0.1µF from signal to GND): 90°
- Pull-up resistor (10k from signal to VCC): 90°
- Pull-down resistor (10k from signal to GND): 90°
- Output dc-blocking capacitor (10µ from output to load): 90°


### Passive Components — Feedback (R, C near op-amp)

**Principle**: When near op-amp, prefer vertical to show feedback loop.

**Rule**: **90°** (vertical) when positioned in same column as nearby op-amp
- Feedback resistors (Rf) in same column as op-amp: 90°
- Compensation capacitors in same column as op-amp: 90°
- Vertical orientation shows the feedback loop clearly
- GRID_COL_MM / 2 (~6.35 mm) determines "same column"

**Fallback**:
- Input/preconditioning/output block roles: prefer **0°** (horizontal)
- Series passive rule applies when not near op-amp

**Examples**:
- Feedback resistor (Rf = 100k in op-amp column): 90° (vertical feedback path)
- Compensation capacitor: 90° (tight feedback loop)


### Passive Components — Input/Preconditioning (R, C with INPUT/PRECONDITIONING role)

**Principle**: Support left-to-right signal flow through input stage.

**Rule**: Prefer **0°** (horizontal)
- Supports signal entry from left
- Shows progression toward op-amp
- Consistent with series-passive convention

**Examples**:
- Input coupling capacitor: 0°
- Volume control resistor: 0°
- Bias network components: 0°


### Passive Components — Output/Stage (R, C with OUTPUT role)

**Principle**: Support left-to-right signal flow through output stage.

**Rule**: Prefer **0°** (horizontal)
- Supports signal exit to the right
- Shows progression from op-amp toward output
- Consistent with series-passive convention

**Examples**:
- Output coupling capacitor: 0°
- Load isolation resistor: 0°
- Output network components: 0°


### Passive Components — Decoupling (R, C with DECOUPLING role)

**Rule**: Typically **0°** by default (no signal pins present)
- Currently treated as isolated power-to-power connections
- May benefit from vertical (90°) orientation for clarity if both pins are power
- Not enforced by current heuristics (all-power case is edge case)

**Note**: Future enhancement could explicitly handle decoupling-only passives


### Diodes (D*)

**Principle**: Standard forward-bias series placement.

**Rule**: Always **0°** (horizontal)
- Anode on left, cathode on right
- Supports left-to-right current flow
- Matches convention for series signal components


## Consistency Across Similar Parts

**Key Principle**: Similar components in the same functional role should have
consistent orientations.

**Implementation**:
1. **Block roles guide orientation**: Components assigned to same block role
   (e.g., FEEDBACK, INPUT, OUTPUT) follow the same convention.
2. **Topology guides orientation**: Shunt vs. series topology is detected
   automatically, ensuring consistent orientation across similar passive types.
3. **Position-based fallback**: For series passives without explicit block role,
   position heuristic ensures consistent left-to-right alignment.

**Benefits**:
- Readers quickly learn visual patterns
- Schematic grammar is consistent
- Reduced cognitive load when scanning similar stages


## Implementation Details

### compute_orientations() Function

Located in `kicad_pcb/layout.py`, implements all conventions above.

**Parameters**:
- `ir`: CircuitIR (parsed circuit)
- `positions`: {ref: (x, y)} component positions
- `tiers`: Optional tier assignments for connectors
- `roles`: Optional connector role assignments ("input"/"output")
- `block_layout`: Optional BlockLayout with block role assignments

**Priority Order**:
1. Connector orientation (by role or tier)
2. Op-amp orientation (always 0°)
3. Passive orientation (by block role, shunt topology, or position heuristic)
4. Diode orientation (always 0°)
5. Default to 0°

### Block Roles (from block_detection.py)

- `INPUT`: Input connector or input-stage component
- `PRECONDITIONING`: Input conditioning / volume / bias
- `OPAMP_CORE`: Operational amplifier or active gain stage
- `FEEDBACK`: Feedback network around op-amp
- `OUTPUT`: Output coupling or terminal
- `POWER_ENTRY`: Power supply entry point
- `DECOUPLING`: Power supply filter / decoupling

### Shunt Topology Detection

Passives are detected as "shunt" if they have:
- ≥1 pin connected to a power net (GND, VCC, V+, V-, etc.)
- ≥1 pin connected to a signal net
- Result: Automatic 90° rotation

### Position Heuristic

For series passives (all pins on signal nets):
- Sum vertical distances (|Δy|) to signal-net neighbors
- Sum horizontal distances (|Δx|) to signal-net neighbors
- If |Δy| > |Δx|: rotate 90° (vertical)
- Otherwise: 0° (horizontal)

Power/ground nets are excluded from this calculation so they don't bias
the orientation decision.


## Testing

**Unit Tests**: `tests/unit/test_phase9_orientation.py`
- TestSeriesPassiveOrientations: 2 tests
- TestShuntPassiveOrientations: 3 tests
- TestFeedbackPassiveOrientations: 2 tests
- TestConnectorOrientations: 3 tests
- TestOpAmpOrientations: 1 test
- TestInputStagePassiveOrientations: 2 tests
- TestOutputStagePassiveOrientations: 1 test
- TestConsistencyWithinRoles: 2 tests
- **Total: 16 tests, all passing** ✅

**Integration Tests**: `tests/integration/test_phase9_integration.py`
- Tests full layout pipeline with orientation expectations
- Validates tier-based connector orientation
- Validates series/shunt/feedback orientations in realistic circuit


## Future Enhancements (Phase 9.2–9.3)

- **Phase 9.2**: Normalize similar part presentation (ensure visual consistency
  within the same functional stage)
- **Phase 9.3**: Add comprehensive orientation sanity tests
  - Connector orientation consistency at page edges
  - Op-amp orientation uniformity across all ICs
  - Passive orientation consistency within blocks
