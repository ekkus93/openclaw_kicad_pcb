# Actionable failures for 5v-linear-voltage-regulator-eurorack

- Source file: **5v-linear-voltage-regulator-eurorack.kicad_sch**
- Result: **fail**
- Total score: **0.00**
- Electrical status: **failed**

## Sub-scores

- intrinsic `validity` = 20.00
- intrinsic `overlap` = 0.00
- intrinsic `page_bounds` = 0.00
- intrinsic `routing_simplicity` = 11.50
- intrinsic `label_strategy` = 10.00
- intrinsic `spread` = 8.42
- intrinsic `power_symbols` = 10.00
- similarity `role_counts` = 0.00
- similarity `relative_positions` = 1.14
- similarity `label_strategy` = 0.00
- similarity `geometry_spread` = 0.00
- similarity `wire_stub_ratio` = 15.00

## Guidance

Do not special-case this fixture. Fix generic generator/evaluator rules instead.

## Failures

- [high] `electrical_equivalence` — Generated schematic connectivity differs from the source fixture. (edit: src/kicad_pcb/router.py, src/kicad_pcb/commands/_sch_apply.py)
- [medium] `geometry_spread` — generated x-column spread is significantly worse. (edit: src/kicad_pcb/graphviz_layout/dot_builder.py, src/kicad_pcb/graphviz_layout/snap.py)
- [medium] `relative_positions` — major-symbol ordering diverges from the source fixture. (edit: src/kicad_pcb/graphviz_layout/dot_builder.py, src/kicad_pcb/graphviz_layout/snap.py)
- [low] `label_strategy` — local labels dominate the schematic. (edit: src/kicad_pcb/router.py)
- [low] `label_strategy` — generated label/global-label strategy diverges from the source. (edit: src/kicad_pcb/router.py)
- [low] `overlap` — layout lints reported potential crowding or composition issues. (edit: src/kicad_pcb/schematic_metrics.py, src/kicad_pcb/corpus/layout_features.py, src/kicad_pcb/evaluation/)
- [low] `page_bounds` — symbols extend beyond a single A4 page envelope. (edit: src/kicad_pcb/evaluation/)
- [low] `role_counts` — generated symbol-role counts diverge from the source fixture. (edit: src/kicad_pcb/block_detection.py)
