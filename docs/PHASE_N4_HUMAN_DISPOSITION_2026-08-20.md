# Phase N4 Human Disposition Record

Date prepared: 2026-08-20
Branch: `webapp`
Phase: N4 — Human disposition
Source fixture manifest: `tests/fixtures/refinement/evaluation_corpus/manifest.json`
Governing TODO: `docs/KICAD_SCHEMATIC_ELECTRICAL_INVARIANCE_AI_VISUAL_REFINEMENT_TODO_2026-08-10.md`

## Purpose

This record is the human-review layer for the Phase N3 experimental schematic-refinement corpus. It must be completed only from a Phase N3 evidence bundle that has passed the full 12-fixture acceptance gate. N4 judgments are experimental evidence; they do not by themselves change production heuristic weights, operation rules, or electrical semantics.

## Evidence binding

Fill these fields from the accepted Phase N3 run before recording any disposition:

- N3 workflow run URL/ID: **PENDING**
- N3 artifact name/ID: **PENDING**
- N3 implementation SHA: **PENDING**
- Provider: **PENDING**
- Model: **PENDING**
- Source manifest SHA-256: **PENDING**
- Baseline expectations SHA-256: **PENDING**
- N3 acceptance result/report: **PENDING**

Do not complete N4 against a partial, failed, stale, or non-hash-bound N3 artifact. The preferred acceptance record is the JSON emitted by `kicad-refine-eval-validate`; copy its implementation/provider/model/corpus-hash bindings here before assigning any fixture disposition.

## Objective review packet

Generate `phase-n4-review-packet.json` with `kicad-refine-eval-review` only after the N3 evidence passes acceptance. The packet preserves the exact fixture order and supplies the non-subjective inputs needed below: before/after render paths, baseline/final metrics and numeric deltas, critic output, plan output, apply-once/refine operation evidence, refine iteration summary, and stop reason. Its disposition/reason fields remain `PENDING` by design.

The packet is an aid to review, not a substitute for visual judgment. Final dispositions must still be based on direct inspection of the accepted before/after PNGs and the supporting evidence.

## Disposition rubric

Each fixture receives exactly one final disposition:

- **improved** — the final accepted schematic is materially easier to read or trace overall, and any local regressions do not outweigh the improvement.
- **neutral** — the final result is effectively unchanged, or improvements and regressions balance without a clear net gain.
- **worse** — the final accepted schematic is materially harder to read or trace overall, including cases where a deterministic metric improves but visible layout quality regresses.

Electrical correctness is not a human-review variable here. N3 must already prove final electrical invariance before a fixture is eligible for N4.

For each fixture, inspect at minimum:

1. `manifest.json` and the known visual defects/categories.
2. The `before_after.before_render` PNG.
3. The `before_after.after_render` PNG.
4. Baseline/analyze metrics and `refine/final/metrics.json`.
5. `operations.json` plus refine result/rejection history.
6. The final stop reason.

Record concrete visual evidence rather than generic statements such as “looks cleaner.” When deterministic metrics and the human disposition disagree, record the exact disagreement rather than changing the disposition to match the metric.

## Corpus review

### 1. `n1-crowded-power-regulator`

Categories: `crowded_layout`, `excessive_wire_bends`

Known defects:
- Dense symbol packing and a large power-symbol population make local grouping difficult to read.
- Several routes contain unnecessary orthogonal bends and short segments.

- Disposition: **PENDING**
- Concrete human-review reasons: **PENDING**
- Relevant metric deltas: **PENDING**
- Accepted operations / rejected operations: **PENDING**
- Stop reason: **PENDING**
- Metric/human disagreement: **PENDING**
- Critic failure or hallucination observed: **PENDING**
- Operation-vocabulary gap observed: **PENDING**

### 2. `n1-repeated-current-source`

Categories: `inconsistent_repeated_blocks`, `crowded_layout`

Known defects:
- Four repeated current-source channels are arranged with enough local variation to exercise repeated-block consistency review.
- The repeated channels contain dense local symbol groupings and overlapping readability regions.

- Disposition: **PENDING**
- Concrete human-review reasons: **PENDING**
- Relevant metric deltas: **PENDING**
- Accepted operations / rejected operations: **PENDING**
- Stop reason: **PENDING**
- Metric/human disagreement: **PENDING**
- Critic failure or hallucination observed: **PENDING**
- Operation-vocabulary gap observed: **PENDING**

### 3. `n1-power-heavy-multiunit`

Categories: `poor_power_symbol_organization`, `label_readability_collisions`
Special case: `multi_unit_symbol`

Known defects:
- A very large power-symbol population competes with the functional signal path for visual attention.
- Dense labels and symbol text create multiple readability conflicts across the sheet.

- Disposition: **PENDING**
- Concrete human-review reasons: **PENDING**
- Relevant metric deltas: **PENDING**
- Accepted operations / rejected operations: **PENDING**
- Stop reason: **PENDING**
- Metric/human disagreement: **PENDING**
- Critic failure or hallucination observed: **PENDING**
- Operation-vocabulary gap observed: **PENDING**

### 4. `n1-uart-flow-orientation`

Categories: `poor_left_to_right_signal_flow`, `awkward_connector_orientation`
Special case: `unnamed_net`

Known defects:
- The interface signal path does not present as a consistently progressive left-to-right flow.
- Endpoint placement and orientation make the external connection direction harder to scan than necessary.

- Disposition: **PENDING**
- Concrete human-review reasons: **PENDING**
- Relevant metric deltas: **PENDING**
- Accepted operations / rejected operations: **PENDING**
- Stop reason: **PENDING**
- Metric/human disagreement: **PENDING**
- Critic failure or hallucination observed: **PENDING**
- Operation-vocabulary gap observed: **PENDING**

### 5. `n1-buck-support-passives`

Category: `support_passives_detached_from_primary_ic`

Known defect:
- Support passives are spread across several local groups instead of reading as one compact primary-IC support network.

- Disposition: **PENDING**
- Concrete human-review reasons: **PENDING**
- Relevant metric deltas: **PENDING**
- Accepted operations / rejected operations: **PENDING**
- Stop reason: **PENDING**
- Metric/human disagreement: **PENDING**
- Critic failure or hallucination observed: **PENDING**
- Operation-vocabulary gap observed: **PENDING**

### 6. `n1-can-connector-flow`

Categories: `awkward_connector_orientation`, `poor_left_to_right_signal_flow`

Known defects:
- The transceiver and external bus endpoint do not form a clean directional visual chain.
- The external connection geometry makes interface direction less immediately legible.

- Disposition: **PENDING**
- Concrete human-review reasons: **PENDING**
- Relevant metric deltas: **PENDING**
- Accepted operations / rejected operations: **PENDING**
- Stop reason: **PENDING**
- Metric/human disagreement: **PENDING**
- Critic failure or hallucination observed: **PENDING**
- Operation-vocabulary gap observed: **PENDING**

### 7. `n1-microsd-crossings`

Categories: `avoidable_wire_crossings`, `excessive_wire_bends`

Known defects:
- The sheet contains numerous non-junction wire crossings that increase tracing effort.
- Several signal routes use more orthogonal turns than the local geometry appears to require.

- Disposition: **PENDING**
- Concrete human-review reasons: **PENDING**
- Relevant metric deltas: **PENDING**
- Accepted operations / rejected operations: **PENDING**
- Stop reason: **PENDING**
- Metric/human disagreement: **PENDING**
- Critic failure or hallucination observed: **PENDING**
- Operation-vocabulary gap observed: **PENDING**

### 8. `n1-solar-support-spacing`

Categories: `support_passives_detached_from_primary_ic`, `crowded_layout`

Known defects:
- Support components do not consistently read as tightly associated with the primary control function.
- Several local symbol groups are visually crowded.

- Disposition: **PENDING**
- Concrete human-review reasons: **PENDING**
- Relevant metric deltas: **PENDING**
- Accepted operations / rejected operations: **PENDING**
- Stop reason: **PENDING**
- Metric/human disagreement: **PENDING**
- Critic failure or hallucination observed: **PENDING**
- Operation-vocabulary gap observed: **PENDING**

### 9. `n1-tft-label-density`

Categories: `label_readability_collisions`, `poor_left_to_right_signal_flow`

Known defects:
- Very high local-label density creates repeated text and wire readability conflicts.
- Multiple interface branches compete for a single obvious left-to-right signal-flow narrative.

- Disposition: **PENDING**
- Concrete human-review reasons: **PENDING**
- Relevant metric deltas: **PENDING**
- Accepted operations / rejected operations: **PENDING**
- Stop reason: **PENDING**
- Metric/human disagreement: **PENDING**
- Critic failure or hallucination observed: **PENDING**
- Operation-vocabulary gap observed: **PENDING**

### 10. `n1-stm32-spread`

Categories: `excessive_spread`, `label_readability_collisions`
Special cases: `multi_unit_symbol`, `explicit_no_connect`

Known defects:
- Functional groups occupy a large fraction of the available sheet and are separated by long visual travel distances.
- The combination of many labels, long routes, and distributed power symbols reduces scan efficiency.

- Disposition: **PENDING**
- Concrete human-review reasons: **PENDING**
- Relevant metric deltas: **PENDING**
- Accepted operations / rejected operations: **PENDING**
- Stop reason: **PENDING**
- Metric/human disagreement: **PENDING**
- Critic failure or hallucination observed: **PENDING**
- Operation-vocabulary gap observed: **PENDING**

### 11. `n1-tpic-crossings-unnamed`

Categories: `avoidable_wire_crossings`, `excessive_wire_bends`
Special case: `unnamed_net`

Known defects:
- The signal network contains many non-junction crossings that make connectivity harder to follow.
- Long routes and repeated turns increase visual path length around the central device.

- Disposition: **PENDING**
- Concrete human-review reasons: **PENDING**
- Relevant metric deltas: **PENDING**
- Accepted operations / rejected operations: **PENDING**
- Stop reason: **PENDING**
- Metric/human disagreement: **PENDING**
- Critic failure or hallucination observed: **PENDING**
- Operation-vocabulary gap observed: **PENDING**

### 12. `n1-w5500-crowded-power`

Categories: `crowded_layout`, `poor_power_symbol_organization`, `label_readability_collisions`

Known defects:
- Dense decoupling and termination networks crowd the primary interface IC and nearby signal routes.
- Power symbols and text are interleaved with signal wiring in a way that increases visual clutter.

- Disposition: **PENDING**
- Concrete human-review reasons: **PENDING**
- Relevant metric deltas: **PENDING**
- Accepted operations / rejected operations: **PENDING**
- Stop reason: **PENDING**
- Metric/human disagreement: **PENDING**
- Critic failure or hallucination observed: **PENDING**
- Operation-vocabulary gap observed: **PENDING**

## Aggregate N4 findings

Complete only after all 12 fixture dispositions are final.

### Disposition counts

- Improved: **PENDING**
- Neutral: **PENDING**
- Worse: **PENDING**

### Deterministic-metric disagreement categories

**PENDING**

For each disagreement, record the fixture, metric name/delta, human disposition, and why the metric failed to capture the visible result.

### Critic failure patterns / hallucinations

**PENDING**

Separate at least:
- false-positive issue identification;
- missed obvious defect;
- incorrect object binding;
- visually unsound desired outcome;
- repeated/non-actionable recommendation;
- recommendation outside the supported operation vocabulary.

### Operation-vocabulary gaps

**PENDING**

For each gap, record the fixture, desired visual repair, why no current registered operation safely expresses it, and whether it appears suitable for a future deterministic primitive.

## N4 completion gate

N4 is complete only when:

- [ ] every one of the 12 fixtures has exactly one `improved`, `neutral`, or `worse` disposition;
- [ ] every disposition has concrete visual reasons;
- [ ] all observed deterministic-metric/human-review disagreements are recorded;
- [ ] recurring critic failure patterns/hallucinations are summarized;
- [ ] operation-vocabulary gaps are summarized;
- [ ] the record is bound to the exact accepted N3 workflow run, artifact, implementation SHA, provider, model, and corpus hashes.

After this gate is complete, use this record together with `operations.json` from the accepted N3 corpus to produce the Phase N5 heuristic-discovery report. Do not modify production heuristic weights/rules directly from N4 judgments.
