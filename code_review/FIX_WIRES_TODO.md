# FIX_WIRES_TODO

## Current baseline

- Active router baseline file: `/home/ubo/work/openclaw_kicad_pcb/kicad-pcb/src/kicad_pcb/router.py`
- Active focused regression file: `/home/ubo/work/openclaw_kicad_pcb/tests/unit/test_phase6_wire_simplification.py`
- Current review artifact: `/home/ubo/kicad-projects/sessions/ne5532_headphone_amp_fa070cbe/ne5532_headphone_amp_preview_20260312_230958/svg/OpenClaw_Managed.svg`
- Matching PNG: `/home/ubo/kicad-projects/sessions/ne5532_headphone_amp_fa070cbe/ne5532_headphone_amp_preview_20260312_230958/png/OpenClaw_Managed.png`
- Prior review baseline: `/home/ubo/kicad-projects/sessions/ne5532_headphone_amp_fa070cbe/ne5532_headphone_amp_preview/svg/OpenClaw_Managed.svg`
- Prior comparison baseline: `/home/ubo/kicad-projects/sessions/ne5532_headphone_amp_fa070cbe/ne5532_headphone_amp_preview_20260312_152500/svg/OpenClaw_Managed.svg`

## Current problems

1. The input side does not yet read as a simple `J1 -> C5/R1 -> RV1 -> U1` signal chain.
2. The secondary local input net still forms a rectangular detour around the `C5/R1` area.
3. The two input-side local nets are still visually interleaved too tightly.
4. The input neighborhood is still taller than necessary.
5. The `RV1` connection still looks like a branch off a routing structure instead of a downstream continuation.
6. The router is still choosing local lanes that are close enough to nearby bodies that the final geometry reads like obstacle avoidance instead of intentional signal flow.

## Fix plan

### 1. Lock the primary input path

Status: DONE

- Goal: make `LEFT_IN` visually read as the dominant connector-entry path.
- Code area: `/home/ubo/work/openclaw_kicad_pcb/kicad-pcb/src/kicad_pcb/router.py`
- Proposed fix:
	- [x] keep the existing connector-led asymmetric lane heuristic for `LEFT_IN`
	- [x] do not broaden it to unrelated ladder groups
	- [x] preserve current output-side behavior unchanged
- Implemented result:
	- connector-led vertical ladder groups now assign the entry net `LEFT_IN` to a dedicated left-entry lane
	- current NE5532 fixture locks `LEFT_IN -> ("vertical", 49.53)` while `IN_L_AC -> ("vertical", 57.15)`
	- the route-level regression now locks in the direct `J1 -> LEFT_IN` entry segment on the left side
- Acceptance criteria:
	- `LEFT_IN` still uses the left-entry lane in the NE5532 fixture
	- `J1` enters directly into the primary input lane without reverting to the older symmetric pattern
	- completed in preview `..._212047` with the primary lane moved off the older symmetric `x=52.07` path

### 2. Remove the secondary rectangular lane

Status: DONE

- Goal: make `IN_L_AC` stop drawing a box around the `C5/R1` neighborhood.
- Code area: `/home/ubo/work/openclaw_kicad_pcb/kicad-pcb/src/kicad_pcb/router.py`
- Proposed fix:
	- [ ] add a targeted planner rule for the secondary lane in the connector-led input ladder group
	- [x] prefer a shorter downstream continuation lane instead of a full-height parallel box
	- [x] avoid changing `LEFT_IN` while doing this
- Current result:
	- [x] the old full-height secondary trunk at `x=57.15` from `y=101.60` to `y=142.24` is gone
	- [x] total wire count dropped from `115` to `114`
	- [x] repo-wide validation is green again after the protected-endpoint wire-orientation fix
	- [x] the smaller local `C5/R1` box is gone in preview `..._230958`; `IN_L_AC` now continues as a single right-side vertical lane at `x=60.96`
- Acceptance criteria:
	- [x] the `IN_L_AC` path no longer forms a visible rectangle around `C5/R1`
	- [x] the resulting shape is simpler than preview `ne5532_headphone_amp_preview_20260312_212047`
	- [x] no regression on the output-side `C7/R7/J2` neighborhood

### 3. Reduce vertical span of the input cluster

Status: IN PROGRESS

- Goal: compress the `J1/C5/R1/RV1` area so it reads as one compact local stage.
- Code area: `/home/ubo/work/openclaw_kicad_pcb/kicad-pcb/src/kicad_pcb/router.py`
- Proposed fix:
	- [ ] keep the two local nets on distinct lanes when needed
	- [ ] shorten unnecessary full-height vertical runs in the input group
	- [ ] prefer lane endpoints tied to actual participating taps instead of spanning the whole local box
- Current diagnosis:
	- [x] `LEFT_IN` / `IN_L_AC` are no longer the dominant height problem after item 2; their local planner output is already compact
	- [x] the remaining vertical span is now dominated by the adjacent `VOL_L_OUT` neighborhood (`R4`, `RV1` pin `2`, `U1` pin `3`)
	- [x] a first positioned compact-route chooser experiment was tried and rejected because the preview did not reduce overall local height and increased junction count
	- [ ] next change should target that `VOL_L_OUT` local route without regressing the now-clean `LEFT_IN` / `IN_L_AC` shape
- Acceptance criteria:
	- the input neighborhood uses less vertical height than preview `..._212047`
	- the path remains electrically clear and does not reintroduce merged/shared trunks

### 4. Make `RV1` feel downstream

Status: TODO

- Goal: make the potentiometer connection read as the next stage in the chain instead of a side branch.
- Code area: `/home/ubo/work/openclaw_kicad_pcb/kicad-pcb/src/kicad_pcb/router.py`
- Proposed fix:
	- [ ] bias the `IN_L_AC` continuation toward the `RV1` side once the `C5/R1` local node is established
	- [ ] avoid a high wraparound connection if a shorter downstream continuation exists
- Acceptance criteria:
	- `RV1` visually reads as continuing the input path
	- the local geometry looks less like a bus and more like a staged signal chain

### 5. Add explicit regression coverage for the intended shape

Status: DONE

- Goal: stop relying only on visual memory of previews.
- Test file: `/home/ubo/work/openclaw_kicad_pcb/tests/unit/test_phase6_wire_simplification.py`
- Proposed fix:
	- [x] keep the existing asymmetric `LEFT_IN` regression
	- [x] add a targeted route-level regression for the secondary input lane so it cannot revert to a full rectangle
	- [x] assert stable lane coordinates or stable key segments, not brittle full-wire snapshots
- Acceptance criteria:
	- [x] tests fail when the input-side secondary lane returns to a boxy rectangle
	- [x] tests do not over-constrain unrelated output-side routing

### 6. Regenerate and review after each routing change

Status: IN PROGRESS

- Goal: catch visual regressions immediately.
- Commands:

```bash
python kicad-pcb/scripts/kicad_pcb.py new-from-netlist \
	--name <preview_name> \
	--netlist /home/ubo/work/openclaw_kicad_pcb/code_review/ne5532_headphone_amp_netlist.json \
	--symbols-dir /usr/share/kicad/symbols \
	--mode kicad

kicad-cli sch export svg \
	--output <preview_dir>/svg \
	<preview_dir>/OpenClaw_Managed.kicad_sch
```

- Acceptance criteria:
	- compare each new preview directly against `..._212047`, `..._152500`, and `..._094046`
	- do not accept a change solely because tests pass
	- prefer the preview that produces the clearest input-side chain while preserving the improved output side

- Current review status:
	- [x] regenerated and reviewed preview `..._212047`
	- [x] regenerated and reviewed preview `ne5532_headphone_amp_preview`
	- [x] regenerated and reviewed preview `ne5532_headphone_amp_preview_20260312_230958`
	- [ ] regenerate after item 3 is implemented
	- [ ] regenerate after item 4 is implemented

## Validation checklist

- [x] `uv run --frozen ruff check .`
- [x] `uv run --frozen mypy kicad-pcb/src`
- [x] `uv run --frozen pytest -q`
- [x] `uv run --frozen pytest -q tests/unit/test_phase6_wire_simplification.py tests/unit/test_phase6_coverage.py tests/unit/test_block_detection.py -k 'chain or ladder or spine or zero_length or vplus or collision_safe'`
- `uv run --frozen pytest -q tests/unit/test_phase6_wire_simplification.py -k 'chain or ladder or spine or zero_length or vplus or collision_safe'`
- `uv run --frozen ruff check kicad-pcb/src/kicad_pcb/router.py tests/unit/test_phase6_wire_simplification.py`
- `uv run --frozen mypy kicad-pcb/src/kicad_pcb/router.py`

## Implementation order

1. Keep `LEFT_IN` as the stable primary lane.
2. Fix the `IN_L_AC` rectangular secondary lane.
3. Reduce overall input-cluster height.
4. Refine the `RV1` continuation shape.
5. Re-run preview generation and visual comparison after each step.
