# kicad-pcb Skill — Memory File

## 2026-05-28T17:54:10Z - GPT-5.4 - Revalidated the corpus closeout state after the router gate recovery

- The repo validation gate is still green on the retained router/evaluation state: `uv run ruff check .`, `uv run mypy src/kicad_pcb src/kicad_pcb_web`, `uv run pytest`, and `uv run pytest -m requires_kicad` all pass (`2489 passed, 1 skipped`; `23 passed` on the KiCad-backed slice).
- A fresh full corpus evaluation now completes cleanly across all 18 fixtures with no runtime partials, but the current retained generator/evaluator state still fails `electrical_equivalence` on all 18 fixtures. The dominant remaining work is generic net identity/connectivity recovery, not harness stability.
- The batch-2 TODO and original corpus TODO now document that current reality explicitly: representative mismatches remain on nets like `+12V`, `12MHZ_CLK`, `+5V`, `SW`, `CUR1_OUT`, `+3.3V_LOCAL`, and `+3.3V@SD` instead of the earlier runtime-partial bucket.

## 2026-05-28T06:54:24Z - GPT-5.4 - Cleared the multi-unit split-ref runtime blocker for USB hub and STM32

- Multi-unit symbol emission now keeps synthetic placed refs internal for layout/routing but restores logical refs in emitted symbol instances and bind markers, and `SCH003` now permits duplicate logical refs only for distinct-unit placements of the same `lib_id`.
- That moved `4-port-usb-20-hub-w-2-internal-ports-and-2-external-ports` and `stm32g030-minimal-system-circuit-electrical` out of the evaluation-runtime/not-run bucket and back into ordinary electrical-equivalence failures. The remaining work on those fixtures is now broader connectivity/routing drift (`12MHZ_CLK` / `D2-` over-connection on USB hub; `BATT_POST` and net-name collapse on STM32), not `_sch_apply` reference leakage.

## 2026-05-28T15:40:27Z - GPT-5.4 - Restored the repo gate after the final router/writeout regressions

- `router.py` now limits emitted power-symbol clustering to dense same-net groups, so Phase 10 readability still collapses the over-fragmented NE5532 GND symbols without merging ordinary two-symbol unit-test cases.
- The late 4+-pin protected-stub fallback now keeps compact local vertical groups like the headphone amp `MID_RAIL` net fully wired while still breaking out truly wide foreign-attachment cases to endpoint labels.
- The repo gate is green again on this state: `uv run ruff check .`, `uv run mypy src/kicad_pcb src/kicad_pcb_web`, and `uv run pytest` all pass (`2489 passed, 1 skipped`).

## 2026-05-28T06:37:09Z - GPT-5.4 - Closed the MicroSD electrical blocker with retained power-attachment and junction-write fixes

- `microsd-card-in-spi-mode-with-hotswap-support` now lands as `pass_with_warnings` on both a focused fixture run and the full 18-fixture corpus re-run. The retained generic closeout sequence was: expand dense endpoint-label breakout search before reusing occupied anchors, route direct per-pin power symbols through protected anchor planning, route slash-power fallback labels through the same protected anchor planner in `write_routing(...)`, and split explicit router junctions into real wire endpoints before simplification/writeout so KiCad export keeps intended branch joins like `R64 pin 1`.
- The full corpus baseline improved from 17 failed/partial fixtures to 15 after these retained fixes. Solar and MicroSD are now both out of the active Phase 5.2 blocker set, so the next representative electrical surfaces are the remaining `rp2040`, USB hub, and STM32 failures.

## 2026-05-28T02:21:59Z - GPT-5.4 - Cleared the batch-2 runtime bucket and checkpointed the first retained generator-quality follow-up

- Batch-2 no longer has any `evaluation_runtime` partial fixtures. `rp2040-microcontroller-core-circuit-mitayi-pico-d1` now evaluates normally after `SymbolIndex.get_pins()` was taught to return an empty set for genuine pin-free symbols with a complete definition chain, while still raising `SYMBOL_HAS_NO_PINS` for broken `extends` chains.
- `solar-charger-mppt-circuit-sts1-pcb-sidepanel` also now evaluates normally after `graphviz_layout/dot_builder.py` stopped wrapping feedback dummy nodes in a `cluster_feedback` subgraph. The equivalent flat dummy-node edges avoid Graphviz 12's `flat_reorder` assertion when block-zone anchors are active.
- Phase 5.1 in `docs/MODEL_KICAD_CORPUS2_TODO.md` is now effectively complete: focused tests for the retained harness fixes are green, a full ingest refresh still reports 18 accepted / 0 partial / 0 rejected fixtures, and all 9 batch-2 fixtures now produce ordinary evaluation reports.
- A first retained label-attachment refinement is also in place in `router.py`: already-occupied label coordinates now outrank route length in `_label_attachment_plan()`, which reduced some duplicate-label collisions and shaved current mismatch counts on at least `rp2040` and `4-port-usb`, but it did not yet eliminate the remaining shared-anchor failures across the batch-2 generator-quality fixtures.

## 2026-05-28T05:55:57Z - GPT-5.4 - Closed the solar electrical blocker with retained routing and passive-pin-order fixes

- `solar-charger-mppt-circuit-sts1-pcb-sidepanel` now lands as `pass_with_warnings` on the full 18-fixture corpus re-run instead of failing electrical equivalence. The retained generic closeout sequence was: actually wire the perpendicular foreign-attachment breakout preference into `_append_pin_endpoint_labels(...)`, mirror two-pin passives in `_write_symbols(...)` when the mirrored pin order better matches connected-net centroids and avoids foreign endpoint collisions, and make multi-pin power clusters abandon shared routes that still cross protected foreign attachment points.
- The new solar-safe baseline proves the remaining Phase 5.2 work is no longer centered on the old lower-left analog cluster. The next representative electrical blockers are now the broader `rp2040`, USB hub, STM32, and MicroSD fixtures, while the repo-level focused regressions for the new retained fixes are green.

## 2026-05-28T02:00:43Z - GPT-5.4 - Completed corpus batch-2 Phase 4 triage and validated the docs checkpoint

- `docs/MODEL_KICAD_CORPUS2_TODO.md` now assigns all 9 new batch-2 fixtures to concrete generic failure buckets instead of fixture-specific placeholders, and all per-fixture `Triage complete` boxes are checked.
- The two retained runtime/harness buckets are now explicit: `rp2040-microcontroller-core-circuit-mitayi-pico-d1` fails on a pin-free `Mechanical:MountingHole` extends chain during evaluation/apply, while `solar-charger-mppt-circuit-sts1-pcb-sidepanel` fails deterministically in Graphviz `dot` with the `flat_reorder` assertion.
- The seven generator-quality batch-2 failures cluster around reusable categories rather than one-off quirks: netlist export identity mismatches and relative-position drift hit all 7, geometry spread hits 6, label/global-label strategy hits all 7, and the dominant domain buckets are power/ground topology, connector/shared-lane routing, and compact local decoupling placement.
- The repo gate was rerun successfully on this Phase 4 docs/status checkpoint too: `uv run ruff check .`, `uv run mypy src/kicad_pcb src/kicad_pcb_web`, and `uv run pytest` all passed (`2470 passed, 1 skipped`).

## 2026-05-28T01:55:44Z - GPT-5.4 - Completed corpus batch-2 Phase 3 evaluation and revalidated the repo gate

- `src/kicad_pcb/corpus/embedded_symbols.py` now preserves alias-backed embedded symbol base names when child subsymbol names imply a hidden local root, then emits a base-plus-alias materialization shape so KiCad 9 can load fixtures like the `SamacSys_Parts:NCV317MBSTT3G` current-source schematic during corpus evaluation.
- `src/kicad_pcb/evaluation/reports.py` now converts per-fixture runtime exceptions into structured partial `evaluation_runtime` reports instead of aborting `model-corpus evaluate`, so the full 18-fixture sweep completes and writes actionable failures for the two remaining runtime-partial batch-2 fixtures.
- Batch-2 corpus Phase 3 is now complete in `docs/MODEL_KICAD_CORPUS2_TODO.md`: all 9 new fixtures are marked ingested and evaluated, with the current split recorded as 7 electrical-plus-layout failures and 2 structured runtime partials (`rp2040-microcontroller-core-circuit-mitayi-pico-d1`, `solar-charger-mppt-circuit-sts1-pcb-sidepanel`).
- The full repo gate was rerun successfully on this post-Phase-3 state: `uv run ruff check .`, `uv run mypy src/kicad_pcb src/kicad_pcb_web`, and `uv run pytest` all passed (`2470 passed, 1 skipped`).

## 2026-05-28T01:32:02Z - GPT-5.4 - Restored the repo gate and completed corpus batch-2 Phase 2 ingest

- The repo gate is green again after the late-snap/layout cleanup: `uv run ruff check .`, `uv run mypy src/kicad_pcb src/kicad_pcb_web`, and `uv run pytest` all pass, including the formerly red NE5532/layout regression cluster.
- The final gate cleanup in this pass was mostly tolerance-safe regression maintenance around the retained geometry (`tests/unit/test_netlist_commands.py`, `tests/unit/test_phase7_regression_guardrails.py`) plus a stale unused import removal in `tests/unit/test_phase4_layout.py`; the generator fixes remained in `snap.py`, `router.py`, `block_detection.py`, `layout.py`, and `dot_builder.py`.
- Batch-2 corpus ingest is now complete: `uv run python -m kicad_pcb.cli model-corpus ingest --source-dir model_kicad_files --out-dir tests/fixtures/model_corpus --refresh --require-kicad` produces 18 accepted fixtures total (9 original + 9 new), and every new batch-2 fixture is `ready` with `metadata.json`, `source.kicad_sch`, `source_normalized.kicad_sch`, `source_layout_features.json`, `source_netlist.kicadxml`, and `circuit_ir.json`.

## 2026-05-28T00:09:47Z - GPT-5.4 - Cleared the focused NE5532 bend-count blocker, but the branch still has a broader late-snap regression cluster

- `src/kicad_pcb/router.py` now scores compact local analog alternatives with a lighter per-junction penalty and a slightly stronger bend penalty, so the analog-audio profile no longer forces chain routing for the real NE5532 `BUF_L_IN` / `HP_L_OUT` local nets purely to avoid implicit lane taps. The focused real-NE5532 route-quality regression is green again with `bend_count <= 80`.
- The small-analog routing debug surface is still preserved for the same six NE5532 candidate nets: chain-only follower/local-loop cases still report `heuristic_override="small_analog_local_routing"`, and the shared-lane/spine fallbacks now also keep that override marker when the analog profile explicitly evaluated them.
- Even with the NE5532 blocker fixed, the full repo gate is not green yet. The current branch still has a broader regression cluster centered on earlier late-snap/layout changes (`test_block_detection`, `test_phase4_layout`, `test_phase5_power_clustering`, `test_phase6_wire_simplification`, and `test_phase8_layout`), so corpus Phase 11 work should stay paused until that larger gate is stabilized.

## 2026-05-27T22:57:05Z - GPT-5.4 - Restored split-unit late-snap behavior for the real NE5532 path; one route-quality metric still blocks the gate

- `classify_circuit(...)` now augments unsplit multi-unit op-amp refs with synthetic per-unit signal-role assignments (for example `U1A` inheriting `OPAMP_CORE` and `U1B` getting `BUFFER_STAGE` when the unit-level follower motif is present). That restored the late split-unit snap passes on the real NE5532 fixture without changing the unsplit source IR.
- The late snap pipeline also now refreshes same-column skip-pair protection immediately before the last deoverlap pass, which preserves intentionally compact `PRECONDITIONING` / `INTERSTAGE` local node columns after the final unit-specific shaping instead of re-spreading them back out.
- With those fixes in place, the focused real-NE5532 slice in `tests/unit/test_netlist_commands.py` is down to a single remaining failure: `test_new_from_real_ne5532_fixture_keeps_route_quality_metrics_bounded` still reports `bend_count=89` (threshold `<= 80`), while the rest of the focused U1A/U1B geometry/readability assertions are green again.

## 2026-05-27T16:51:45Z - GPT-5.4 - MicroSD now preserves the named control nets; the remaining blocker is the U21 power/GND cluster

- The retained MicroSD router improvements now include: per-pin endpoint global labels for slash-prefixed connector/global-label breakout paths, direct-route protected-point scoring that includes foreign raw pin endpoints, and a third direct-route detour radius for crowded two-pin nets. Focused regressions for those retained behaviors are green again.
- On `microsd-card-in-spi-mode-with-hotswap-support`, those retained changes materially improved export identity: `CMD_MOSI@SD`, `DET_B`, `~SD_CARD_ENABLE`, `Net-(J8-DAT1)`, and `Net-(J8-DAT2)` now export with the correct pin memberships instead of being dropped or merged into the left-side bundle.
- The remaining electrical-equivalence blocker is now concentrated in the U21 power/GND area. KiCad export still places `U21` pins `7/8/10` onto `/SPI.MISO`, leaves `C37` pin 2, `C42` pin 1, `R58` pin 1, and `R60` pin 1 effectively unconnected, and keeps `+3.3V`, `+3.3V@SD`, and `GND` memberships wrong.
- One new experiment was tried and explicitly rejected in this state: making the compact power-cluster acceptance checks respect protected foreign pin endpoints made `+3.3V` even worse (it collapsed to only `R62` pin 1), so that change was reverted. The next useful fix surface is the still-active compact/clustered GND and power topology around U21, not the already-improved named control nets.

## 2026-05-27T16:12:54Z - GPT-5.4 - Retained the first bundle-aware MicroSD routing fixes, but export identity is still blocked

- The branch now retains three new generic router changes on top of the earlier scoped-power/scoped-label work: `_best_direct_route_with_protected_points(...)` now considers a second detour radius for crowded 2-pin routes, 4+ pin bus-style nets can fall back from a colliding mean spine to a lower-collision existing shared lane when `_route_candidate_key(...)` proves it is better, and `_label_attachment_plan(...)` now avoids future protected stub endpoints instead of only already-occupied wire/label points.
- Those retained changes are covered by new focused regressions in `tests/unit/test_phase6_wire_simplification.py`, and the focused MicroSD routing/corpus slice is green again.
- On the actual `microsd-card-in-spi-mode-with-hotswap-support` fixture, the retained fixes materially changed the left bundle: `/DET_B` now survives as a named exported net and the old `y=119.38` DET_B spine is no longer the dominant route. But electrical equivalence still fails because exported identity is still wrong for `DET_A`, `Net-(J8-DAT2)`, `~SD_CARD_ENABLE`, and the shifted `+3.3V@SD` / `DET_B` pin memberships.
- The next useful MicroSD target is no longer the original DET_B trunk geometry. The stronger current clue is that the generated geometry is better but KiCad export still merges or drops some connector-side named nets, so the next debugging surface is the remaining label/export semantics around those improved routes rather than more broad direct-route scoring experiments.

## 2026-05-27T10:36:17Z - GPT-5.4 - Refined the MicroSD routing blocker from a single bad detour into a shared left-bundle lane problem

- The branch is still at the prior retained best state: scoped power-name normalization and slash-net global-label handling remain, the focused MicroSD routing/corpus slice is green, and `model-corpus evaluate --fixture microsd-card-in-spi-mode-with-hotswap-support` is still electrically failing.
- Re-running the incremental routing trace showed the left-side collapse is not just `/DET_A` or `/DET_B` in isolation. `/DET_A` still draws the first bad direct segment at `y=143.51`, `/DET_B` then adds the large mean-Y spine at `y=119.38`, and later `Net-(J8-DAT1)` also reuses the same `x=2.54` left escape lane.
- The important new conclusion is that the dense MicroSD failure is now best understood as a **shared left-bundle lane** problem: several independent nets are being routed through the common connector-side `x=2.54` escape corridor, so local tweaks to direct-route scoring or slash-label breakout alone do not fix electrical equivalence. The next useful fix surface is bundle-aware routing/ordering for these connector-side nets rather than another small candidate-scoring change.

## 2026-05-27T10:26:01Z - GPT-5.4 - Narrowed the remaining MicroSD blocker to a direct-route collision choice before the DET_B spine

- The retained generic fixes in this segment are still the scoped power-name normalization and scoped-net global-label handling in routed/debug paths; focused regressions remain green with those changes, but `model-corpus evaluate --fixture microsd-card-in-spi-mode-with-hotswap-support` is still electrically failing.
- I proved the first left-bundle electrical merge now appears **before** `/DET_B`: after routing `/DET_A`, the generated schematic already contains a multi-label connected component combining `/DAT0_MISO@SD` and `/DET_A`. Later `/DET_B` adds a large spine that worsens the collapse, but it is not the first root cause anymore.
- Recomputing `/DET_A` in isolation showed a safe detour exists, but `_best_direct_route_with_protected_points(...)` chooses the shorter colliding route once the full `dynamic_protected_points` set is included. That makes the next likely fix area the direct-route candidate scoring logic rather than another label-type or block-classification tweak.

## 2026-05-27T07:43:47Z - GPT-5.4 - Finished the interrupted MicroSD label-breakout refactor, but the fixture is still electrically blocked

- `src/kicad_pcb/router.py` now fully uses `_label_attachment_plan(...)` instead of the older `_label_attachment_point(...)`, and the focused regression suite around protected chain routing, connector-label breakouts, and label-anchor fallback is green again.
- The retained planner behavior now scores multiple candidate breakout anchors, prefers a dedicated short breakout route over falling straight back to the pin endpoint, and keeps later label placements aware of already-emitted wires and label anchors within the same routing pass.
- Despite that refactor completing cleanly, `model-corpus evaluate --fixture microsd-card-in-spi-mode-with-hotswap-support` still fails electrical equivalence. The current best diagnosis remains a small set of KiCad export collisions centered on slash-prefixed global-label anchors in the dense left-side MicroSD bundle, especially interactions involving `+3.3V@SD`.

## 2026-05-27T07:20:11Z - GPT-5.4 - MicroSD tuning is still blocked by KiCad export collapsing the dense left-side bundle

- The retained generic MicroSD changes so far are: protected-point-aware 3-pin chain routing, occupied-wire protection for later routed nets, and connector-label breakout for wide slash-prefixed connector-attachment nets. Focused routing/corpus tests are green with those changes, but the fixture still fails electrical equivalence.
- The current export symptom is that KiCad still collapses most of the dense left-side MicroSD bundle into a few nets. In the best retained state, exported net names are still only `+3.3V@SD`, `DET_A`, `GND`, `SD_CS@SD`, and `SPI.MISO`, with exported `+3.3V@SD` incorrectly absorbing many signal and ground pins while exported `GND` shrinks to only `C40` pin 1.
- One additional experiment was explicitly rejected and reverted: forcing dense GND areas to per-pin GND symbols made the collapse worse instead of better. The next useful debugging target is the exact remaining overlap/connectivity pattern in the left-side bundle, not the MAX232 fixes or the repo-wide test gate.

## 2026-05-27T06:52:19Z - GPT-5.4 - MAX232 now passes after a narrower fallback GND routing change

- `dual-ttl-uart-to-rs232-max232-reference-design` now passes electrical equivalence again. The retained generic fixes are the earlier charge-pump decoupling/orientation changes plus a router fallback rule that turns aligned two-pin fallback `GND` clusters into direct per-pin `power:GND` symbol attachments instead of a shared lane that KiCad 9 can partially drop.
- A minimal one-pin sanity check proved `MAX232` `U2` pin 15 exports correctly on `GND` by itself, so the remaining failure was in KiCad's handling of the shared fallback route shape rather than in `_transform_pin_at(...)` or the symbol pin geometry.
- Focused validation is green with this retained state, and fixture-local `model-corpus evaluate --fixture dual-ttl-uart-to-rs232-max232-reference-design` now reports electrical equivalence `passed`. The next active Phase 11 tuning target is `microsd-card-in-spi-mode-with-hotswap-support`.

## 2026-05-27T01:48:48Z - GPT-5.4 - Fixed symbol-library fallback and narrowed the remaining MCP2551 export blocker

- `read_lib_symbol_def_flat(..., symbols_dir=None)` now falls through from incomplete repo-local libraries to later symbol directories instead of stopping at the first matching library file. That fixed `power:+5V` lookup, which the repo-local `src/kicad_pcb/resources/symbols/power.kicad_sym` does not provide but the system KiCad library does.
- The compact local power-cluster caller now restores the ordinary pin-to-stub wire segments before appending helper-generated cluster routes, so compact GND / local-decoupling clusters no longer rely on helper output alone for pin continuity.
- The remaining MCP2551 failure is now specifically in KiCad netlist export semantics, not the corpus harness: the generated schematic draws `CAN0_RX` / `CAN0_TX` on the intended U1 left-side wires, but `kicad-cli sch export netlist` still resolves them as U1 pins 5 and 8 and drops the other expected named nets entirely. The next debugging target is the exact written schematic geometry/label strategy that KiCad recognizes for those nets, not power-symbol lookup or the general evaluation pipeline.

## 2026-05-27T01:11:30Z - GPT-5.4 - Restored the full repo gate after the corpus-routing regressions

- The latest repo-wide green gate is confirmed again: `uv run ruff check .`, `uv run mypy src/kicad_pcb src/kicad_pcb_web`, and `uv run pytest` all pass after fixing the post-corpus-unblock routing regressions.
- The key generator correction was in `_transform_pin_at(...)`: transformed pin angles now point outward from the symbol instead of back toward the body, while still applying the KiCad library-Y flip plus counter-clockwise symbol rotation semantics. That change restored correct local follower/output routing and brought the broader netlist/routing tests back into alignment.
- The remaining corpus work is no longer blocked by repo-wide regressions. The next active tuning task is the MCP2551 fixture, followed by MAX232 and MicroSD once MCP2551 has a generic fix.

## 2026-05-26T23:42:25Z - GPT-5.4 - Unblocked corpus evaluation infrastructure and exposed the first real tuning failures

- Full `model-corpus evaluate` now completes across all 9 committed fixtures: evaluation materializes fixture-local embedded symbol libraries, scales Graphviz `dot` timeout with graph size, avoids Graphviz assertion failures from decoupling/feedback constraints, and writes per-fixture actionable reports under `code_review/generated/model_eval/`.
- The MCP2551 tuning loop exposed three generic correctness fixes in the generator/evaluator stack: electrical comparison now safely flattens sheet-scoped KiCad XML net names when that rename is collision-free, generated electrical export now runs against the canonical `generated.kicad_sch` instead of the project root sheet, and `_transform_pin_at(...)` now converts library pin coordinates using KiCad's positive-up library Y axis plus counter-clockwise symbol rotation semantics.
- The current remaining work is generator tuning rather than harness breakage. MCP2551 still fails electrical equivalence because several unlabeled direct-route nets are not yet exporting as expected, while the broader corpus still needs generic placement/routing similarity improvements for fixtures like MAX232 and MicroSD.

## 2026-05-26T23:07:46Z - GPT-5.4 - Applied the corpus normalization patch and unblocked KiCad 9 ingest

- Applied the external normalization patch for model-corpus ingestion by adding `src/kicad_pcb/corpus/normalization.py`, wiring `src/kicad_pcb/corpus/ingestion.py` to preserve raw `source.kicad_sch` while writing `source_normalized.kicad_sch`, and adding focused unit coverage in `tests/unit/test_model_corpus_normalization.py`.
- The repo gate is green after the integration fixes required by the patch: `uv run ruff check .`, `uv run mypy src/kicad_pcb src/kicad_pcb_web`, and `uv run pytest` all pass, and `uv run python -m kicad_pcb.cli model-corpus ingest --source-dir model_kicad_files --out-dir tests/fixtures/model_corpus --refresh --require-kicad` now succeeds with 9 accepted fixtures and 0 partials.
- The old KiCad 9 ingest blocker is resolved: the committed corpus fixtures now include `source_normalized.kicad_sch`, `source_netlist.kicadxml`, and `circuit_ir.json`, with normalization reasons recorded in fixture metadata.
- The next blocker moved downstream into evaluation/generation rather than source ingest: `uv run python -m kicad_pcb.cli model-corpus evaluate --corpus-dir tests/fixtures/model_corpus --out-dir code_review/generated/model_eval` now fails on at least one fixture because symbol resolution cannot find `SamacSys_Parts:ULQ2003AQDRQ1`.

## 2026-05-26T22:45:37Z - GPT-5.4 - Documented the current raw-corpus KiCad blocker for ChatGPT handoff

- **Current blocker summary**: the remaining corpus issue is not KiCad availability or CLI syntax. `kicad-cli 9.0.9` is installed, the corpus workflow now targets `kicad-cli >= 9.0.0`, and the same CLI export path works on known-good schematics. The blocker is that the imported raw source schematics under `model_kicad_files/*.kicad_sch` still fail at the KiCad load step, so `kicad-cli sch export netlist --format kicadxml ...` returns `Failed to load schematic`.
- **What still works**: the repo's own parser (`SchematicDoc`) can read these raw files, corpus ingest still accepts them as parseable sources, and the workflow writes deterministic partial fixtures with metadata plus layout-derived artifacts. The repo gate is green, and the KiCad-backed regression tests are green under KiCad 9 because they now use a known loadable readability schematic rather than these raw imports.
- **Observed real-corpus effect**: all current `model_kicad_files/*.kicad_sch` sources still ingest as partial `layout_only` fixtures. Their fixture directories contain metadata and layout features, but they do not get `source_netlist.kicadxml` or `circuit_ir.json`, and `model-corpus evaluate` skips the real corpus because those required electrical artifacts do not exist yet.
- **Version/format observations**: most raw files currently declare `(version 20231120)`, which maps to KiCad 8-era schematic format, while `model_kicad_files/5v-linear-voltage-regulator-eurorack.kicad_sch` declares `(version 20250114)`, which maps to KiCad 9-era schematic format. So the raw corpus is mixed-format, not a single clean KiCad 9 set.
- **What has already been ruled out**: several likely normalization candidates were tested and did not make KiCad 9 accept the raw source schematics: fixing invalid root UUIDs, adding missing `sheet_instances`, normalizing empty or malformed instance paths, cleaning project/path metadata, deduplicating top-level comments, stripping embedded `lib_symbols`, and broad serializer/rewrite passes.
- **Current best hypothesis**: the raw files contain a KiCad-9-incompatible structural detail that our internal parser tolerates but KiCad's own schematic loader rejects. The failure looks more like a structural/schema issue in sheet-instance or symbol-instance/lib-symbol data than a simple one-field typo.
- **Important implication**: a normal KiCad 8 -> 9 upgrade path does not currently help, because KiCad must successfully load the file before it can resave it in a newer format. Since these raw files fail to load in KiCad 9, any conversion would need to be a custom normalization/rewrite pass that first makes them acceptable to KiCad.
- **Practical next step**: if ChatGPT investigates this further, the target question is not "why is export broken?" in the abstract; it is "what exact structural construct in these mixed-format imported `.kicad_sch` files causes KiCad 9 to reject them while our parser still accepts them?" Once that offending construct is identified, we can decide whether to add a generic normalization pass or keep the real-corpus tuning loop blocked.

## 2026-05-26T22:38:04Z - GPT-5.4 - Switched corpus KiCad coverage to KiCad 9-only and stabilized the repo gate

- The corpus/export workflow now targets `kicad-cli >= 9.0.0`; the repo-wide green gate on this machine is `uv run ruff check .`, `uv run mypy src/kicad_pcb src/kicad_pcb_web`, `uv run pytest`, and `uv run pytest -m requires_kicad`.
- `src/kicad_pcb/evaluation/reports.py` now treats generated/source KiCad XML -> `CircuitIR` validation errors as structured electrical failures on `generated_netlist` rather than raising, and the related unit/integration tests were updated to expect that behavior.
- The KiCad-backed corpus integration tests now use a known KiCad 9-loadable readability schematic instead of the raw `model_kicad_files/*.kicad_sch` imports; the real corpus tuning loop remains blocked because those imported source schematics still fail `kicad-cli sch export netlist`.

## 2026-05-26T21:56:24Z - GPT-5.4 - Landed the model-corpus evaluation slice, docs, and real-corpus fixture generation

- Added the `src/kicad_pcb/evaluation/` package with electrical equivalence comparison, intrinsic quality scoring, source-similarity scoring, actionable report generation, and a reusable `evaluate_model_corpus(...)` workflow.
- Added `model-corpus evaluate` CLI support, `ModelCorpusEvaluateResult`, report formatting, synthetic evaluation tests, and a KiCad-backed integration test that skips cleanly on unsupported KiCad versions.
- Fixed `python -m kicad_pcb.cli ...` so it actually executes `main()` and no longer emits the import-time runtime warning caused by `kicad_pcb.__init__` eagerly importing `cli`.
- Fixed the `requires_kicad` test selection path so `uv run pytest -m requires_kicad` now selects the intended integration suite instead of returning "no tests collected".
- Generated and committed the real `tests/fixtures/model_corpus/` corpus snapshot from the nine current `model_kicad_files/*.kicad_sch` sources; on this machine all nine fixtures are valid partial fixtures with `pending_netlist_export` because `kicad-cli 7.0.11` is too old for repo schematic XML export.
- Added `model_kicad_files/README.md` and `docs/MODEL_KICAD_CORPUS.md` to document the deterministic ingest/evaluate/improve loop and the current KiCad-version caveat.

## 2026-05-26T21:38:16Z - GPT-5.4 - Landed the model-corpus ingestion foundation and KiCad-version gating

- Implemented the first `MODEL_KICAD_CORPUS_TODO` slice: new `src/kicad_pcb/corpus/` modules for metadata/slugging, layout-feature extraction, embedded-symbol extraction, KiCad XML parsing to canonical `CircuitIR`, deterministic report writers, and ingestion/list helpers; added `model-corpus ingest` / `model-corpus list` CLI plumbing plus result formatting.
- Added regression coverage in `tests/unit/` for metadata slugging, real-source power-symbol counting, layout features, embedded-symbol artifacts, KiCad XML import, and corpus ingestion behavior, plus `tests/integration/test_model_corpus_kicad_export.py` for the KiCad-backed ingest path.
- The corrected `count_power_symbols()` now counts real and generated power symbols, so the readability baseline fixture `tests/fixtures/readability/ne5532_headphone_amp_left_current/baseline_metrics.json` was updated from `power_symbols: 0` to the real value `5`.
- Current environment note: the repo's symbol-bearing schematic fixtures are not loadable by `kicad-cli 7.0.11`; integration tests that exercise repo schematic/netlist export now require `kicad-cli >= 8.0.0` and skip cleanly on older KiCad versions.

## 2026-05-26T21:01:19Z - GPT-5.4 - Captured the resolved model-corpus design decisions from replies2

- `docs/replies2.md` settles the model-corpus slug policy: normalize filename stems to lowercase ASCII with non-alphanumeric runs replaced by `-`, use the base slug only when unique, and for any collision group assign every member `base_slug--<hash8>` where `hash8` is the first 8 hex chars of `sha256(normalized_relative_source_path)`.
- Automatic ingestion metadata should default to `license="unknown"`, `source_url=null`, and `notes=[]`, may include non-authoritative detected hints, and should preserve hand-edited metadata on refresh unless an explicit overwrite flag is added later.
- Rejected source schematics should not get fixture directories in V0; they belong only in `ingestion_report.json` and `summary.md`, while accepted/partial fixtures are committed under `tests/fixtures/model_corpus/`.
- V0 embedded-symbol extraction should write deterministic `source_embedded_symbols.sexpr`, not require a valid `.kicad_sym`, and evaluation output under `code_review/generated/model_eval/` should stay uncommitted while exposing a canonical `generated.kicad_sch` path alongside the real managed-sheet output.

## 2026-05-26T20:57:22Z - GPT-5.4 - Added the second corpus-spec clarification handoff for ChatGPT review

- Reviewed `docs/MODEL_KICAD_CORPUS_SPEC.md` and `docs/MODEL_KICAD_CORPUS_TODO.md` without making code changes and captured the unresolved implementation questions in `docs/responses2.md`.
- The open questions focus on deterministic fixture-id collision handling, metadata provenance defaults, rejected-fixture representation, V0 embedded-symbol artifact format, which generated corpus/evaluation outputs should be committed, and whether evaluation should preserve the current `OpenClaw_Managed.kicad_sch` naming.

## STANDING RULE — memory.md timestamp discipline
**ALWAYS use the actual UTC time (to the second) when adding an entry.**
- For committed work: use `git show -s --format=%ai <hash>` to get the exact commit timestamp.
- For in-session notes (no commit yet): run `date -u +%Y-%m-%dT%H:%M:%SZ` at the moment of writing.
- NEVER fabricate or round timestamps (e.g. `T00:00:00Z`, `T01:00:00Z`, `T03:25:00Z`, future dates, or year-2025 dates).
- The "Last updated" header must also use the real current time from `date -u`.
- **This rule has been violated repeatedly** (March 2026 dates for February commits, 2025 dates for 2026 commits). Every new entry MUST start with `git show` or `date -u` — never guess or invent a timestamp.

---

## 2026-05-27T18:43:29Z - GPT-5.4 - Refined local-rail decoupling detection heuristics

- Graphviz/layout decoupling detection now needs to keep the original private-net rule for plain signal-to-GND bypass parts while also recognizing supply-like and `*BIAS*` local rails such as `VCC_LOCAL` and `LOCAL_BIAS` as valid decoupling anchors.
- A plain one-signal/one-ground capacitor on a normal signal net like `AUDIO_IN` must still stay vertical as a shunt/bypass part instead of being promoted into the horizontal decoupling lane.
