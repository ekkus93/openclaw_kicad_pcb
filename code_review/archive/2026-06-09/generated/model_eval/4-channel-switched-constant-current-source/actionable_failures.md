# Actionable failures for 4-channel-switched-constant-current-source

- Source file: **4-channel-switched-constant-current-source.kicad_sch**
- Result: **fail**
- Total score: **0.00**
- Electrical status: **failed**

## Sub-scores

- intrinsic `validity` = 20.00
- intrinsic `overlap` = 0.00
- intrinsic `page_bounds` = 10.00
- intrinsic `routing_simplicity` = 10.48
- intrinsic `label_strategy` = 10.00
- intrinsic `spread` = 10.00
- intrinsic `power_symbols` = 10.00
- similarity `role_counts` = 22.00
- similarity `relative_positions` = 0.00
- similarity `label_strategy` = 0.00
- similarity `geometry_spread` = 15.00
- similarity `wire_stub_ratio` = 15.00

## Guidance

Do not special-case this fixture. Fix generic generator/evaluator rules instead.

## Failures

- [high] `electrical_equivalence` — Generated schematic connectivity differs from the source fixture. (edit: src/kicad_pcb/router.py, src/kicad_pcb/commands/_sch_apply.py)
- [medium] `relative_positions` — major-symbol ordering diverges from the source fixture. (edit: src/kicad_pcb/graphviz_layout/dot_builder.py, src/kicad_pcb/graphviz_layout/snap.py)
- [low] `label_strategy` — local labels dominate the schematic. (edit: src/kicad_pcb/router.py)
- [low] `label_strategy` — generated label/global-label strategy diverges from the source. (edit: src/kicad_pcb/router.py)
- [low] `overlap` — layout lints reported potential crowding or composition issues. (edit: src/kicad_pcb/schematic_metrics.py, src/kicad_pcb/corpus/layout_features.py, src/kicad_pcb/evaluation/)
