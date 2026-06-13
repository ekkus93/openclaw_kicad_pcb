# Actionable failures for lmr51450sdrrr-6v-36v-to-500v-4a-buck-converter

- Source file: **lmr51450sdrrr-6v-36v-to-500v-4a-buck-converter.kicad_sch**
- Result: **fail**
- Total score: **0.00**
- Electrical status: **failed**

## Sub-scores

- intrinsic `validity` = 20.00
- intrinsic `overlap` = 0.00
- intrinsic `page_bounds` = 10.00
- intrinsic `routing_simplicity` = 10.34
- intrinsic `label_strategy` = 12.00
- intrinsic `spread` = 10.00
- intrinsic `power_symbols` = 10.00
- similarity `role_counts` = 13.00
- similarity `relative_positions` = 0.00
- similarity `label_strategy` = 0.00
- similarity `geometry_spread` = 0.00
- similarity `wire_stub_ratio` = 13.80

## Guidance

Do not special-case this fixture. Fix generic generator/evaluator rules instead.

## Failures

- [high] `electrical_equivalence` — Generated schematic connectivity differs from the source fixture. (edit: src/kicad_pcb/router.py, src/kicad_pcb/commands/_sch_apply.py)
- [medium] `geometry_spread` — generated x-column spread is significantly worse. (edit: src/kicad_pcb/graphviz_layout/dot_builder.py, src/kicad_pcb/graphviz_layout/snap.py)
- [medium] `relative_positions` — major-symbol ordering diverges from the source fixture. (edit: src/kicad_pcb/graphviz_layout/dot_builder.py, src/kicad_pcb/graphviz_layout/snap.py)
- [low] `label_strategy` — too many global labels are exposed. (edit: src/kicad_pcb/router.py)
- [low] `label_strategy` — generated label/global-label strategy diverges from the source. (edit: src/kicad_pcb/router.py)
- [low] `overlap` — layout lints reported potential crowding or composition issues. (edit: src/kicad_pcb/schematic_metrics.py, src/kicad_pcb/corpus/layout_features.py, src/kicad_pcb/evaluation/)
- [low] `role_counts` — generated symbol-role counts diverge from the source fixture. (edit: src/kicad_pcb/block_detection.py)
- [low] `wire_stub_ratio` — generated routing is noticeably stubbier than the source. (edit: src/kicad_pcb/router.py)
