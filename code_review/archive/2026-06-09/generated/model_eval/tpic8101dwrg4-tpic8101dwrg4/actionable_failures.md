# Actionable failures for tpic8101dwrg4-tpic8101dwrg4

- Source file: **tpic8101dwrg4-tpic8101dwrg4.kicad_sch**
- Result: **fail**
- Total score: **0.00**
- Electrical status: **failed**

## Sub-scores

- intrinsic `validity` = 20.00
- intrinsic `overlap` = 0.00
- intrinsic `page_bounds` = 10.00
- intrinsic `routing_simplicity` = 11.40
- intrinsic `label_strategy` = 7.00
- intrinsic `spread` = 10.00
- intrinsic `power_symbols` = 10.00
- similarity `role_counts` = 7.00
- similarity `relative_positions` = 25.00
- similarity `label_strategy` = 0.00
- similarity `geometry_spread` = 0.00
- similarity `wire_stub_ratio` = 6.00

## Guidance

Do not special-case this fixture. Fix generic generator/evaluator rules instead.

## Failures

- [high] `electrical_equivalence` — Generated schematic connectivity differs from the source fixture. (edit: src/kicad_pcb/router.py, src/kicad_pcb/commands/_sch_apply.py)
- [medium] `geometry_spread` — generated x-column spread is significantly worse. (edit: src/kicad_pcb/graphviz_layout/dot_builder.py, src/kicad_pcb/graphviz_layout/snap.py)
- [low] `label_strategy` — local labels dominate the schematic. (edit: src/kicad_pcb/router.py)
- [low] `label_strategy` — too many global labels are exposed. (edit: src/kicad_pcb/router.py)
- [low] `label_strategy` — generated label/global-label strategy diverges from the source. (edit: src/kicad_pcb/router.py)
- [low] `overlap` — layout lints reported potential crowding or composition issues. (edit: src/kicad_pcb/schematic_metrics.py, src/kicad_pcb/corpus/layout_features.py, src/kicad_pcb/evaluation/)
- [low] `role_counts` — generated symbol-role counts diverge from the source fixture. (edit: src/kicad_pcb/block_detection.py)
- [low] `wire_stub_ratio` — generated routing is noticeably stubbier than the source. (edit: src/kicad_pcb/router.py)
