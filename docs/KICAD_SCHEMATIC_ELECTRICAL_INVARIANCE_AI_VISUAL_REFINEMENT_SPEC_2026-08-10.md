# KiCad Schematic Electrical Invariance and AI Visual Refinement Specification

Date: 2026-08-10
Branch: `webapp`
Planning baseline SHA: `d72ff407460896839893d2221cfb5720013bb9e2`

## 1. Purpose

This specification defines an experimental but production-disciplined subsystem for iteratively improving the visual quality of an electrically correct KiCad schematic using a vision-capable LLM as a critic and planner while preserving the circuit as a hard invariant.

The intended loop is:

`accepted schematic -> render -> visual critique -> structured repair plan -> deterministic candidate edits -> electrical/structural verification -> accept or reject -> repeat`

The subsystem is not a general autonomous schematic author and is not allowed to change circuit semantics. Its job is to improve presentation: component placement, orientation where electrically safe, spacing, alignment, label placement, and wire geometry.

The first implementation must establish the electrical safety boundary before any model-directed mutation is enabled.

## 2. Governing principles

1. **Electrical correctness outranks visual quality.** No visual improvement may be accepted if electrical equivalence cannot be proven.
2. **The LLM is a critic/planner, not an unrestricted editor.** It never directly rewrites arbitrary `.kicad_sch` text or S-expressions.
3. **All edits are explicit deterministic operations.** Every mutation has a typed operation, target object(s), preconditions, postconditions, and an attributable result.
4. **Candidate edits are transactional.** The known-good schematic remains untouched until all hard gates pass.
5. **The actual KiCad artifact is authoritative for verification.** Internal intent is insufficient; the post-edit `.kicad_sch` must be parsed/exported and compared.
6. **Failures are visible and fail closed.** Missing KiCad tooling, malformed model output, unsupported operations, ambiguous object identity, verification mismatch, or validation failure may not degrade into apparent success.
7. **No silent fallback mutation.** If a requested operation cannot be performed exactly under its declared constraints, it is rejected rather than approximated.
8. **Best-known result is retained.** A later iteration cannot destroy an earlier accepted result merely because a model produced a different opinion.
9. **Iteration is bounded.** Maximum rounds, maximum operations, and stopping rules are explicit.
10. **Model scores are advisory.** Deterministic validation and geometry metrics remain independent of subjective LLM judgment.
11. **Every accepted iteration is reproducible and auditable.** Inputs, critique, plan, operations, validation reports, metrics, and before/after artifacts are retained according to an explicit bounded policy.

## 3. Scope

### 3.1 In scope

This batch may add or change:

- reusable electrical-equivalence services currently living under corpus evaluation;
- Circuit IR / generated-schematic semantic fingerprinting required for layout-only mutation safety;
- extraction of logical component inventory, pin/net membership, footprints, and explicit no-connect state;
- transactional schematic candidate creation and promotion;
- deterministic layout mutation primitives;
- deterministic schematic visual-quality metrics and lint integration;
- rendering orchestration for model review;
- vision-capable LLM capability declaration and invocation;
- strict structured critic and repair-plan schemas;
- bounded iterative visual-refinement orchestration;
- CLI/service/webapp endpoints needed to exercise review, plan, and apply modes;
- evidence artifacts and regression fixtures;
- tests and documentation for all of the above.

### 3.2 Explicit non-goals

Unless separately authorized, this batch must not:

- add, remove, or substitute logical circuit components;
- change component references;
- change component values;
- change symbol identities;
- change declared footprints during visual refinement;
- change logical pin-to-net membership;
- join two previously separate electrical nets;
- split one electrical net into multiple nets;
- change intentional no-connect state;
- infer or repair circuit-design mistakes;
- add missing decoupling, pull-ups, protection, filters, connectors, test points, or other electrical design elements;
- perform PCB placement, PCB routing, board outline work, or footprint optimization;
- let a model directly edit KiCad files;
- introduce a broad background-job framework;
- silently replace a vision request with text-only analysis;
- train or fine-tune a model;
- claim objective visual quality from a single LLM score.

Electrical design review may be a future feature, but it must remain separate from this visual-only mutation mode.

## 4. Existing repository foundation

The implementation must reuse existing proven machinery where appropriate rather than fork duplicate concepts.

### 4.1 Circuit IR validation

`src/kicad_pcb/commands/_validate.py` already provides three validation layers for Circuit IR:

1. schema validation;
2. semantic validation such as duplicate refs/nets, zero-pin nets, and unknown component references;
3. symbol/pin validation against the symbol index.

`src/kicad_pcb/commands/_validate_connectivity.py` already provides advisory connectivity findings such as floating components and suspicious single-pin nets.

These remain input-side validation and are not sufficient by themselves to prove that a post-edit KiCad schematic still implements the same circuit.

### 4.2 Existing electrical equivalence

`src/kicad_pcb/evaluation/electrical.py` already compares canonical Circuit IR representations and detects:

- component reference-set differences;
- component value differences;
- component symbol differences;
- net-name-set differences;
- per-net `(ref, pin, unit)` membership differences.

The corpus evaluator already exports a generated schematic through `kicad-cli`, parses the exported KiCad XML netlist, converts it to Circuit IR, and invokes electrical equivalence.

This batch must promote that concept into a reusable production safety boundary rather than leaving it primarily as corpus-evaluation infrastructure.

### 4.3 Existing layout metrics and lint

The repository already contains schematic metrics, wire metrics, layout lint, graph/layout helpers, corpus layout-feature extraction, and intrinsic/source-similarity evaluation.

The new subsystem should compose those capabilities and extend them only where measurements needed by iterative visual refinement are missing.

## 5. Safety boundary: authoritative electrical state

### 5.1 Two complementary baselines

A visual-refinement session must establish two baselines before mutation:

1. **Authoritative Circuit IR baseline** — defines the intended logical circuit.
2. **Accepted schematic baseline** — captures generated-artifact details that must remain unchanged during presentation-only edits but may not be fully represented by the current Circuit IR schema.

The authoritative Circuit IR prevents a bad accepted schematic from becoming the truth merely because it was already generated. The accepted-schematic baseline prevents layout-only work from changing details such as a generated footprint or explicit no-connect state that were not encoded strongly enough in the input IR.

### 5.2 Electrical fingerprint

Implement a reusable `SchematicElectricalFingerprint` or equivalent immutable representation. It must be deterministic and serialization-order independent.

At minimum it must capture:

- logical component refs;
- symbol identity for each logical component;
- value for each logical component;
- footprint identity when present in the authoritative IR or accepted schematic;
- multi-unit identity needed to map unit pins back to the logical component;
- complete logical pin inventory when discoverable from the resolved symbol;
- every electrically connected terminal as `(logical_ref, pin, unit)`;
- named-net identity and its terminal set;
- unnamed-net connected-terminal partitions independent of autogenerated net names;
- explicit intentional no-connect terminals where KiCad represents them;
- any explicitly supported compiler-generated helper symbols that affect electrical extraction.

Coordinates, wire bends, graphical label positions, text positions, and other presentation geometry must not be part of the electrical fingerprint.

### 5.3 Component inventory rule

The post-edit accepted schematic must contain every logical component required by authoritative Circuit IR with the same logical reference and symbol identity.

No logical component may disappear and no unapproved logical component may appear.

If schematic generation requires non-BOM/helper symbols such as power symbols, handling must be explicit and deterministic. Do not suppress arbitrary extra symbols using loose reference-prefix or library-name heuristics. Any helper-symbol classification must be narrow, documented, and regression-tested.

### 5.4 Footprint rule

Visual refinement must not alter footprints.

If authoritative Circuit IR declares a footprint, the generated/accepted component must preserve it exactly after canonical normalization appropriate to KiCad identifiers.

If the Circuit IR does not declare a footprint but the accepted schematic has one, the pre-edit accepted footprint becomes immutable for the visual-refinement session.

A missing footprint must not be silently populated by the visual-refinement subsystem.

### 5.5 Connectivity rule

For every logical terminal, connectivity after an edit must be equivalent to the baseline.

For named nets, preserve both the net identity and exact terminal membership except for narrowly documented normalization such as safe sheet-path prefixes already handled by existing equivalence code.

For unnamed/autogenerated nets, compare connected-terminal partitions rather than unstable autogenerated names.

The verifier must detect at least:

- newly connected pins;
- disconnected pins;
- moved pins between nets;
- merged nets;
- split nets;
- multi-unit ref/unit mapping errors;
- lost intentional no-connects;
- newly intentional no-connect pins;
- component disappearance/addition.

### 5.6 Actual-artifact verification

A candidate may not be accepted solely because an in-memory mutation object claims it preserved connectivity.

For every candidate that reaches final verification:

1. write the candidate `.kicad_sch` to isolated temporary storage;
2. parse it with repository schematic parsing;
3. invoke `kicad-cli` to validate/export the actual schematic netlist when KiCad is required by the selected mode;
4. parse the exported netlist;
5. create the candidate electrical fingerprint;
6. compare against the authoritative and accepted baselines;
7. run required structural/ERC checks;
8. promote only on a full hard-gate pass.

If required KiCad verification cannot run, the apply operation fails. It must not become `pass_with_warning` for production apply mode.

A dedicated test-only mode may exercise lower layers without KiCad, but it must be impossible to confuse that with an accepted production mutation.

## 6. Transactional candidate lifecycle

### 6.1 States

A refinement iteration should have explicit states equivalent to:

- `baseline_ready`
- `rendered`
- `critic_complete`
- `plan_validated`
- `candidate_built`
- `hard_validation_passed`
- `quality_evaluated`
- `accepted`
- `rejected`
- `failed`

The exact implementation may use another representation, but state must be explicit enough to prevent an unverified candidate from becoming accepted.

### 6.2 Candidate isolation

All edits are applied to a candidate copy or in-memory document that is written to a temporary candidate path. The known-good schematic is not modified in place before verification.

### 6.3 Promotion

Promotion to the accepted schematic must be atomic or equivalent under repository filesystem abstractions:

- candidate fully written and fsynced as appropriate;
- hard validation complete;
- candidate still corresponds to the validated bytes/version;
- accepted target replacement succeeds;
- errors leave the prior accepted schematic intact.

If the environment does not permit atomic replacement across filesystems, candidate creation must occur on the same filesystem as the target and use the safest available atomic rename/replace primitive.

### 6.4 Rejection

A rejected candidate must not mutate the accepted artifact. The rejection record must include machine-readable reason codes and enough detail for the planner/model/human to understand why it failed.

## 7. Deterministic mutation vocabulary

### 7.1 General contract

The LLM may request only registered operations. Each operation must define:

- operation type/version;
- stable target identifiers;
- explicit parameters;
- preconditions;
- allowed geometry changes;
- forbidden semantic changes;
- deterministic execution result;
- postcondition verification;
- structured rejection reason.

Unknown operation types fail schema validation. Approximation is not permitted unless the operation contract explicitly defines deterministic snapping behavior.

### 7.2 Initial low-risk operations

The first implementation should prioritize operations such as:

- `move_component`
- `rotate_component`
- `move_label`
- `move_power_symbol`
- `align_components`
- `distribute_components`
- `move_component_group`

All coordinates must obey the configured KiCad grid policy.

### 7.3 Wire-geometry operations

Wire changes have greater risk and should be enabled only after the component-only mutation path is proven.

Candidate operations include:

- `reroute_existing_net_orthogonal`
- `shorten_wire_path`
- `remove_redundant_wire_bend`

They may change only the geometry of an already-established net. End-terminal membership must remain identical.

### 7.4 Deferred high-surface-area operations

The initial experiment should not automatically use operations such as:

- replacing a physical wire with a new net label;
- changing label scope/type;
- converting between global/hierarchical/local labels;
- splitting/merging sheets;
- adding hierarchy;
- renaming nets;
- altering hidden pins;
- replacing symbols;
- changing unit selection.

These may be considered later after additional semantic verification exists.

## 8. Deterministic visual-quality metrics

The subsystem must compute deterministic metrics independently of the LLM before and after each accepted iteration.

Reuse existing metrics/lint where possible and add missing metrics where justified.

The initial metric set should include, when technically reliable:

- component/component overlap count and area;
- component/text overlap;
- wire/text overlap;
- wire/component-body intersection excluding intended terminal entry;
- wire crossing count distinguishing junctions from non-junction crossings;
- total wire Manhattan length;
- total wire segment count;
- total bend count;
- excessive-bend count;
- off-grid geometry count;
- out-of-page or page-bound violation count;
- component spacing distribution;
- alignment/group consistency;
- excessive local density/crowding;
- excessive whitespace/spread indicators;
- repeated-block orientation/spacing consistency where the block detector provides a reliable mapping;
- long-wire count;
- label collision/readability findings;
- power-symbol placement findings already supported by repository lint/metrics.

Metrics must report raw measurements in addition to any aggregate score.

A weighted aggregate may be useful for ranking candidate layouts, but it must not hide raw regressions in hard or high-severity geometry constraints.

## 9. Vision critic

### 9.1 Role

The critic inspects a rendered schematic together with structured schematic context and produces issues. It does not issue filesystem edits and does not directly modify Circuit IR.

### 9.2 Inputs

The critic should receive:

- rendered schematic image at a documented resolution/scale;
- optional crop/tiling metadata for schematics too large to inspect reliably in one image;
- stable component IDs/refs and bounding boxes;
- component symbols/values relevant to visual grouping;
- pin locations and orientations where available;
- logical net membership;
- wire segment geometry;
- junction and label geometry;
- deterministic visual metrics and lint findings;
- prior accepted critiques/changes sufficient to reduce oscillation;
- explicit instruction that electrical semantics are immutable.

Sensitive provider credentials, local absolute paths, and unrelated project data must not be included.

### 9.3 Critic schema

Critic output must use a strict versioned schema. Each issue should include fields equivalent to:

- `issue_id`
- `category`
- `severity`
- `confidence`
- `affected_objects`
- `observation`
- `desired_visual_outcome`
- `evidence`
- `constraints`

Suggested categories include:

- signal flow;
- functional grouping;
- component alignment;
- component spacing;
- component orientation;
- wire crossing/readability;
- excessive wire length/bends;
- label readability;
- power organization;
- repeated-block consistency;
- visual hierarchy;
- whitespace/crowding;
- ambiguous junction/readability.

The model must not be asked to diagnose electrical correctness as part of this schema. If it comments on electrical behavior anyway, those comments are non-authoritative and must not become mutation operations.

### 9.4 Critic rubric

For trend tracking, the critic may also assign rubric scores such as 0-10 for:

- signal-flow readability;
- functional grouping;
- visual hierarchy;
- wire readability;
- component alignment;
- spacing;
- label clarity;
- power organization;
- repeated-block consistency;
- overall readability.

The system must retain the fact that these are model opinions, including model/provider identity and prompt/schema version.

## 10. Repair planner

### 10.1 Role

The planner converts validated critic issues into a bounded list of registered deterministic operations.

The planner may be implemented as a separate LLM invocation or a logically separate structured stage using the same provider/model. The distinction between critic issues and executable operations must remain explicit.

### 10.2 Planner inputs

The planner receives:

- validated critic issues;
- current structured geometry;
- registered operation capabilities and constraints;
- deterministic metrics;
- previous accepted operations;
- rejected operations/reasons from the current session;
- maximum operation budget.

### 10.3 Plan schema

A plan must contain:

- plan/schema version;
- source iteration ID;
- ordered operations;
- issue IDs addressed by each operation;
- expected visual benefit;
- explicit operation arguments;
- optional precondition assertions;
- planner confidence.

The executor does not trust the expected benefit or confidence. It validates the operation and executes deterministically.

### 10.4 Unsupported proposals

If the planner asks for an unsupported operation, the plan is invalid or that operation is explicitly rejected according to the chosen plan-validation policy. The system must not silently map it to a vaguely similar supported operation.

## 11. Vision-provider capability contract

A visual-refinement run may use a provider/model only when the configured LLM capability contract explicitly states that image input is supported in the required request shape.

Do not infer vision support from arbitrary model-name substrings.

If the provider/model does not support vision:

- `review`/`refine` requests requiring image inspection fail with an explicit capability error;
- the system must not silently invoke the model with only textual geometry and claim a vision critique;
- deterministic metrics remain available independently.

Provider response parsing, retry safety, redaction, timeout policy, and failure classification must preserve the previously accepted wizard/LLM production-hardening contracts where the same infrastructure is reused.

## 12. Iteration controller

### 12.1 Modes

Support distinct modes, even if some arrive in later milestones:

- `analyze` — render + deterministic metrics + critic issues; no mutation;
- `plan` — analyze + structured repair plan; no mutation;
- `apply_once` — one candidate plan with hard verification and acceptance/rejection;
- `refine` — bounded multi-round loop retaining the best accepted candidate.

The UI/CLI must not mislabel analyze/plan results as applied changes.

### 12.2 Bounds

Configuration must include explicit limits such as:

- maximum refinement rounds;
- maximum operations per round;
- maximum total accepted operations;
- maximum candidate failures/rejections;
- maximum model invocations consistent with existing retry/repair budgets;
- deterministic candidate/render artifact retention limits.

Defaults must be conservative.

### 12.3 Acceptance policy

A candidate is eligible for acceptance only if:

1. all hard electrical invariance checks pass;
2. structural/KiCad validation passes;
3. no hard geometry validity rule regresses;
4. candidate metrics and/or critic evidence satisfy the configured improvement policy;
5. candidate bytes correspond to the version actually validated.

For the initial experiment, prefer an understandable acceptance rule rather than a complex optimizer. For example:

- never accept electrical/structural regressions;
- never accept increased hard overlap/collision counts;
- accept if at least one targeted deterministic defect improves and no protected metric regresses beyond an explicit tolerance;
- otherwise retain the previous accepted schematic.

Model `overall_readability` score alone must never be sufficient for acceptance.

### 12.4 Best-known candidate

Track the best accepted candidate separately from the latest attempted candidate. A rejected or lower-quality round does not replace it.

### 12.5 Stopping rules

Stop when any of these occurs:

- configured maximum rounds reached;
- critic reports no actionable supported issues above threshold;
- planner produces no valid operations;
- no meaningful deterministic improvement is achieved for the configured number of rounds;
- repeated candidate rejection threshold reached;
- oscillation is detected;
- hard validation/runtime failure requires termination.

Every stop reason must be machine-readable.

## 13. Oscillation and regression control

The controller must retain enough history to detect obvious cycles such as:

- component moved left then right then left;
- rotation toggling between two orientations;
- same wire route repeatedly reintroduced;
- substantially identical geometry fingerprints appearing again.

At minimum, compute a deterministic layout fingerprint for accepted states and reject/refrain from revisiting an identical accepted geometry state.

The critic should receive concise prior accepted decisions and be instructed not to reverse them without identifying a concrete regression. This prompt instruction supplements but does not replace deterministic cycle detection.

## 14. Rendering contract

Rendering supplied to the model must be reproducible enough for comparison.

Record:

- renderer/tool version;
- schematic source hash;
- page/sheet identity;
- image dimensions;
- crop/scale/tiling information;
- rendering options that materially affect visibility.

If the schematic is too large for reliable single-image inspection, use deterministic tiling/crops with stable coordinate mapping. Do not shrink indefinitely until references/text become unreadable.

A model issue referring to a visual region must be resolvable back to stable schematic objects; raw pixel coordinates alone are insufficient for executable planning.

## 15. Evidence and artifacts

Each refinement session should produce bounded structured evidence sufficient to answer:

- What was the starting schematic?
- What did deterministic metrics say?
- What did the critic say was wrong?
- What plan was proposed?
- Which operations were attempted?
- Which operations were rejected and why?
- Did the actual KiCad netlist remain electrically equivalent?
- What structural/ERC checks ran?
- What metrics changed?
- Which candidate was accepted?
- Why did the loop stop?

Suggested artifacts include:

- session manifest JSON;
- baseline electrical fingerprint;
- per-iteration rendered image;
- per-iteration critic JSON;
- per-iteration plan JSON;
- operation-results JSON;
- electrical-equivalence report;
- structural/ERC report;
- metrics before/after;
- candidate/accepted schematic hashes;
- concise human-readable Markdown summary.

Debug/model payload retention must respect existing redaction and retention rules. Raw prompts/completions should not be persisted by default merely because visual-refinement evidence is enabled.

## 16. Failure taxonomy

Introduce explicit failures rather than overloaded generic errors. Exact names may differ, but the subsystem must distinguish at least:

- `VISION_CAPABILITY_UNAVAILABLE`
- `SCHEMATIC_RENDER_FAILED`
- `CRITIC_OUTPUT_INVALID`
- `PLAN_OUTPUT_INVALID`
- `UNSUPPORTED_LAYOUT_OPERATION`
- `AMBIGUOUS_LAYOUT_TARGET`
- `LAYOUT_OPERATION_PRECONDITION_FAILED`
- `LAYOUT_OPERATION_REJECTED`
- `CANDIDATE_WRITE_FAILED`
- `KICAD_VALIDATION_UNAVAILABLE`
- `KICAD_NETLIST_EXPORT_FAILED`
- `ELECTRICAL_INVARIANCE_FAILED`
- `STRUCTURAL_VALIDATION_FAILED`
- `QUALITY_REGRESSION_REJECTED`
- `OSCILLATION_DETECTED`
- `NO_ACTIONABLE_IMPROVEMENT`

Transport/provider errors remain distinguishable from model schema failures and from deterministic executor/verification failures.

## 17. Security and trust boundaries

Model output is untrusted input.

Therefore:

- strict schema validation is mandatory;
- refs/object IDs must resolve against the current accepted schematic version;
- no arbitrary file paths from model output;
- no shell commands from model output;
- no arbitrary S-expression fragments from model output;
- coordinates and numeric values must be finite and bounded;
- operation counts must be bounded before execution;
- stale plans must not apply to a changed schematic;
- candidate promotion must verify the exact validated version;
- provider credentials and local absolute paths must remain redacted;
- image attachment or provider upload identifiers must not become reusable authority to mutate unrelated sessions.

## 18. Test strategy

### 18.1 Electrical-invariance unit tests

Cover at minimum:

- identical circuit passes despite geometry changes;
- missing component fails;
- extra logical component fails;
- symbol change fails;
- value change fails;
- footprint change fails;
- one pin moved to another named net fails;
- two nets merged fails;
- one net split fails;
- unnamed-net autogenerated name changes with identical terminal partition pass;
- unnamed-net terminal change fails;
- multi-unit logical-ref normalization is correct and not over-broad;
- intentional no-connect loss/change fails;
- sheet-scoped safe name normalization remains narrow;
- helper-symbol policy cannot hide arbitrary unexpected components.

### 18.2 Transaction tests

Cover:

- failed candidate leaves accepted schematic byte-identical;
- verification failure cannot promote;
- candidate write failure cannot damage accepted file;
- stale validated candidate cannot replace a newer accepted version;
- promotion preserves exact validated bytes;
- cleanup failures are visible without converting a rejected candidate into success.

### 18.3 Mutation-operation tests

For every registered operation:

- schema validation;
- target resolution;
- grid snapping policy;
- boundary checks;
- collision/precondition behavior;
- deterministic output;
- semantic invariance after application;
- explicit rejection for unsupported/ambiguous requests.

### 18.4 Critic/planner tests

Use deterministic fake providers for:

- valid structured critique;
- malformed JSON/schema;
- unknown refs;
- unsupported operation;
- excessive operation count;
- stale iteration ID;
- model refusal/truncation/no-content;
- vision capability unavailable;
- no silent text-only fallback.

### 18.5 Integration tests with KiCad

Permanent CI or a required KiCad integration job must include fixtures proving that a real generated candidate can be exported through `kicad-cli` and compared against the authoritative circuit.

Negative fixtures must prove that intentional mutations to component inventory and connectivity are caught.

### 18.6 Iteration-loop tests

Cover:

- one-round improvement;
- multi-round improvement;
- no-op stop;
- repeated-rejection stop;
- oscillation/cycle stop;
- best-known candidate retention;
- metrics regression rejection;
- hard validation termination;
- bounded model/operation counts.

## 19. Experimental evaluation corpus

The first useful evaluation should use approximately 10-20 electrically valid schematics representing a range of visual defects, including:

- crowded but correct layouts;
- overly spread layouts;
- poor left-to-right signal flow;
- detached support passives;
- avoidable wire crossings;
- excessive wire bends;
- inconsistent repeated blocks;
- awkward connector orientation;
- poor power-symbol organization;
- label collisions or unclear net presentation.

For each fixture record:

- baseline electrical equivalence;
- baseline deterministic metrics;
- baseline rendered image;
- round-by-round issues and operations;
- final electrical equivalence;
- final deterministic metrics;
- before/after render;
- human disposition: improved / neutral / worse;
- notable repeated repair patterns.

The experiment is successful if it demonstrates that the system can repeatedly improve a meaningful subset of poor-but-correct schematics without changing circuit semantics and without requiring arbitrary direct model edits.

Human review remains important during this experimental phase because the purpose is partly to discover which visual judgments should later become deterministic layout heuristics.

## 20. Learning deterministic rules from accepted repairs

The subsystem should make accepted repair data easy to aggregate later, but automatic self-modification is out of scope.

We want to be able to identify patterns such as:

- decoupling capacitors repeatedly moved closer to associated ICs;
- related pull resistors consistently aligned near relevant pins;
- connectors consistently reoriented for clearer signal flow;
- repeated subcircuits normalized to a common spacing/orientation;
- common wire-crossing fixes that can be expressed deterministically.

Those patterns may later become explicit tested layout heuristics, reducing dependence on the LLM for common cases.

No heuristic may be automatically promoted from model history into production code without an explicit implementation/review step.

## 21. Implementation sequencing

The implementation must proceed in this order unless the specification is amended:

### Phase A — Electrical invariance foundation

- reusable production electrical fingerprint/equivalence service;
- authoritative IR plus accepted-schematic baseline;
- footprint/no-connect coverage;
- actual KiCad export verification;
- negative regression fixtures.

**Exit gate:** intentional semantic mutations are reliably rejected and real geometry-only mutations pass.

### Phase B — Transactional layout candidate infrastructure

- isolated candidate generation;
- version/hash binding;
- hard validation pipeline;
- atomic promotion/rejection;
- evidence records.

**Exit gate:** no failed/unverified candidate can alter accepted state.

### Phase C — Low-risk deterministic mutation primitives

- move/rotate/align/distribute/group/label/power-symbol operations;
- grid/geometry preconditions;
- per-operation tests.

**Exit gate:** operations are deterministic and electrically invariant.

### Phase D — Deterministic quality metrics

- integrate existing metrics/lint;
- add missing crossing/collision/bend/alignment measurements;
- raw before/after report.

**Exit gate:** candidate quality can be evaluated independently of an LLM.

### Phase E — Vision critic and structured planner

- explicit vision capability;
- reproducible render input;
- strict schemas;
- fake-provider and real-provider contract tests;
- no direct editing.

**Exit gate:** model output can produce bounded valid issues/plans without bypassing the deterministic executor.

### Phase F — One-round apply

- critic -> planner -> candidate -> hard validation -> quality decision -> promotion/rejection.

**Exit gate:** representative fixtures show safe accepted improvement and safe rejection.

### Phase G — Bounded iterative refinement

- best-known state;
- cycle detection;
- stop policies;
- bounded evidence retention.

**Exit gate:** multi-round tests prove bounded convergence behavior and no semantic drift.

### Phase H — Experimental corpus evaluation

- 10-20 poor-but-valid fixtures;
- before/after evidence;
- human review;
- identify deterministic heuristic candidates.

## 22. Acceptance criteria

This batch is complete only when all of the following are true:

1. A reusable production electrical-equivalence API exists outside corpus-only orchestration.
2. Every production apply/refine mutation verifies the actual candidate KiCad artifact.
3. Component inventory is preserved against authoritative Circuit IR.
4. Component symbol/value/footprint invariants are enforced.
5. Pin/net connectivity invariants are enforced for named and unnamed nets.
6. Multi-unit semantics are handled correctly.
7. Explicit no-connect state is preserved or unsupported cases fail closed.
8. Required KiCad verification being unavailable prevents production acceptance.
9. Candidate edits are transactional and cannot corrupt the known-good schematic on failure.
10. The LLM can invoke only registered deterministic layout operations.
11. Model output cannot provide arbitrary file/S-expression edits.
12. Deterministic visual metrics are recorded before and after accepted iterations.
13. Vision support is an explicit provider capability; no silent text-only fallback exists.
14. Critic and planner outputs use strict versioned schemas.
15. Analyze/plan/apply/refine modes are truthfully distinguished.
16. Multi-round refinement is explicitly bounded and retains the best-known accepted state.
17. Oscillation/no-improvement stopping behavior is tested.
18. Negative electrical-mutation fixtures fail reliably.
19. Representative geometry-only fixtures pass electrical invariance.
20. The permanent Python/frontend/browser/KiCad/package CI jobs applicable to touched code pass on the exact accepting SHA.
21. Completion evidence identifies implementation-start SHA, product acceptance SHA, final documentation SHA, exact CI runs/jobs, known deferrals, and any separately scoped dependency/security findings.

## 23. Required implementation discipline

During implementation:

- do not weaken existing validation to make a fixture pass;
- do not special-case individual refs or fixture names in production algorithms;
- do not catch broad exceptions and continue as if a mutation succeeded;
- do not treat `kicad-cli` export failure as electrical equivalence;
- do not assume a wire that visually touches a pin is electrically connected without actual artifact verification;
- do not assume an LLM-reported improvement is real without deterministic evidence and hard gates;
- do not modify PCB routing/placement as part of this work;
- document every deliberate fallback and justify why it cannot silently mask a failure;
- prefer explicit rejection over hidden approximation.

## 24. Expected result

At completion, the repository should support a safe experimental workflow in which a poor-but-electrically-correct schematic can be rendered, critiqued by a vision-capable LLM, converted into explicit bounded layout tasks, edited through deterministic operations, verified against the actual KiCad electrical artifact, and iteratively improved without permitting visual optimization to change the circuit.

The longer-term goal is not permanent dependence on the LLM for every layout decision. Accepted repair history should help identify reliable schematic-layout heuristics that can later become deterministic rules, leaving the LLM primarily as a visual reviewer for cases that remain difficult to encode algorithmically.
