# KiCad schematic visual-refinement operator guide

Date: 2026-08-20

This guide consolidates the production-facing Phase P contract for the bounded AI-assisted visual
refinement system. It supplements the implementation specification and runtime-configuration
record; it does not broaden the mutation or electrical-safety contract.

## Purpose and non-goal

Visual refinement may rearrange an already generated, electrically accepted schematic to improve
readability while preserving exact component inventory and electrical connectivity under the
refinement invariance rules. It is **not electrical-design correction**. The critic/planner must not
be treated as authority for changing parts, values, footprints, nets, pin assignments, or design
intent.

## Mode semantics

- **`analyze`** renders the accepted schematic, computes deterministic metrics, builds the
  vision-object map, and obtains a structured visual critique. It verifies that accepted schematic
  bytes did not change.
- **`plan`** performs `analyze` and converts actionable critic issues into a validated bounded list
  of registered deterministic operations. It still does not mutate the accepted schematic.
- **`apply_once`** runs one validated plan against an isolated candidate, executes the deterministic
  operation batch, verifies electrical/structural/geometry/quality gates, and atomically promotes
  only a passing candidate. No-op or rejected rounds preserve accepted bytes.
- **`refine`** repeats the analyze/plan/apply process under explicit round, operation, rejection,
  and model-repair bounds. It retains the best known accepted state and terminates on a normal stop
  reason or re-raises a hard failure.

Production HTTP and `kicad-refine` use the same trusted wizard/current-job composition boundary.
The separate `kicad-refine-eval` command is an evaluation harness and must use isolated fixture
output/work roots; it is not an arbitrary-path production mutation interface.

## Required tooling and capabilities

Production electrical verification requires:

- KiCad 9 (`kicad-cli >= 9.0.0`);
- a generated schematic that belongs to the current trusted wizard/job workspace;
- the authoritative validated `CircuitIR` for that exact generated job;
- a configured and enabled LLM provider/model with explicit `vision_enabled=true`;
- the normal rendering/rasterization dependencies used by the application environment.

Graphviz remains part of normal schematic generation/layout tooling where the project requires it,
but the refinement hard gate is based on the actual KiCad schematic and KiCad exports, not a
Graphviz representation.

Missing vision capability fails with `VISION_CAPABILITY_UNAVAILABLE`; missing/old KiCad or export
failure fails closed. There is no text-only or verification-skipping production fallback.

## Supported deterministic operation vocabulary

The production operation registry contains exactly these operation types:

1. `move_component`
2. `rotate_component`
3. `move_label`
4. `move_power_symbol`
5. `align_components`
6. `distribute_components`
7. `move_component_group`
8. `remove_redundant_wire_bend`
9. `shorten_wire_path`
10. `reroute_existing_net_orthogonal`

Every operation is schema validated, bound to the source schematic hash, constrained by the
operation policy, and resolved against explicit schematic identities/geometry. Unknown operation
types are rejected.

## Unsupported high-risk operations

The refinement layer intentionally does not provide model-directed operations for:

- adding, deleting, or replacing components;
- changing references, values, symbol identities, or footprints;
- adding, deleting, renaming, splitting, or merging nets;
- changing pin assignments or terminal membership;
- changing no-connect state;
- injecting arbitrary labels/connectivity or KiCad S-expressions;
- arbitrary code, shell commands, executable paths, or user-selected KiCad binaries;
- fuzzy component/reference matching;
- unrestricted free-form routing or creation of new electrical topology.

Requests outside the registered vocabulary fail rather than being approximated.

## Electrical-invariance guarantees and limits

A candidate is compared to immutable authoritative/accepted baselines and to exports from the
**actual candidate KiCad file**. The hard gate covers component inventory, exact logical net terminal
partitions, accepted/authoritative footprint constraints, explicit no-connect terminals, and the
narrowly defined treatment of KiCad power-helper symbols. Native KiCad topology is used to recover
only exact unnamed-net partitions that XML export can omit.

The system additionally requires structural/ERC no-regression relative to the accepted baseline and
transaction/hash consistency before promotion.

These guarantees mean visual refinement cannot intentionally change electrical meaning and be
accepted. They do **not** prove that the original electrical design is functionally correct, that a
baseline with existing ERC findings is ideal, or that deterministic readability metrics perfectly
match human visual judgment.

## Configuration bounds and defaults

Iterative refinement is disabled by default. The validated environment contract is:

| Variable | Default | Allowed |
| --- | ---: | --- |
| `KICAD_WEBAPP_REFINEMENT_ENABLED` | `false` | `0`, `1`, `false`, `true` |
| `KICAD_WEBAPP_REFINEMENT_MAX_ROUNDS` | `3` | integer `1..20` |
| `KICAD_WEBAPP_REFINEMENT_MAX_OPERATIONS_PER_ROUND` | `4` | integer `1..32` |
| `KICAD_WEBAPP_REFINEMENT_MAX_TOTAL_ACCEPTED_OPERATIONS` | `8` | integer `1..128` |
| `KICAD_WEBAPP_REFINEMENT_MAX_CANDIDATE_REJECTIONS` | `2` | integer `1..20` |
| `KICAD_WEBAPP_REFINEMENT_MAX_CRITIC_REPAIRS` | `0` | integer `0..8` |
| `KICAD_WEBAPP_REFINEMENT_MAX_PLANNER_REPAIRS` | `0` | integer `0..8` |

Values are strict canonical text; unknown `KICAD_WEBAPP_REFINEMENT_*` variables are rejected so a
typo cannot silently fall back to a default. The logical refinement-layer model-call ceiling is:

`max_rounds * (2 + max_critic_repairs + max_planner_repairs)`

Provider transport retries retain provider/HTTP-client semantics and are not represented as a
separate aggregate wall-clock guarantee.

## Evidence locations and retention

For a trusted wizard session/current generation job, production derives refinement state below:

```text
<data_dir>/wizard_sessions/<wizard-session-id>/refinement/<latest-job-id>/work
<data_dir>/wizard_sessions/<wizard-session-id>/refinement/<latest-job-id>/evidence
```

Each completed iteration publishes sanitized atomic evidence. Accepted, rejected, and no-op rounds
are retained. Session evidence includes a machine-readable `manifest.json` and human-readable
`summary.md`, with provider/model provenance, hashes, decisions, metrics, operations, verification
outcomes, and stop/failure disposition without credentials or provider-private payloads.

The canonical generated `.kicad_sch` remains authoritative. `project.zip` and
`schematic_preview.png` are derived artifacts and are refreshed after a committed refinement; an
optional preview failure is warning-visible and cannot redefine canonical schematic truth.

The Phase N live-evaluation workflow uses separate per-run output/work roots and uploads its
completed evidence bundle/log according to `docs/PHASE_N3_LIVE_EVALUATION.md`.

## Normal stop reasons

Normal bounded-loop stop reasons are:

- `REFINEMENT_STOP_MAX_ROUNDS`
- `REFINEMENT_STOP_NO_ACTIONABLE_ISSUES`
- `REFINEMENT_STOP_NO_OPERATIONS`
- `REFINEMENT_STOP_NO_MEANINGFUL_IMPROVEMENT`
- `REFINEMENT_STOP_REJECTION_LIMIT`
- `REFINEMENT_STOP_OSCILLATION`
- `REFINEMENT_STOP_OPERATION_BUDGET`

A hard runtime/provider/verification failure is re-raised. The service attempts to record a failed
session disposition with `REFINEMENT_STOP_HARD_FAILURE` while preserving the original
machine-readable failure code as primary. Explicit user cancellation is not implemented.

## Important hard-failure families

Operators should preserve machine-readable codes when diagnosing failures. Important families
include:

- `VISION_CAPABILITY_UNAVAILABLE` — configured refinement runtime is not explicitly vision capable;
- `KICAD_CLI_MISSING` — required KiCad 9 CLI is absent/too old;
- `REFINEMENT_RENDER_FAILED`, `REFINEMENT_RENDER_TOO_LARGE`,
  `REFINEMENT_RENDER_GEOMETRY_MISMATCH`, `REFINEMENT_RENDER_SHEET_AMBIGUOUS` — render cannot be
  trusted for visual reasoning;
- `ELECTRICAL_INVARIANCE_FAILED` — exported candidate topology/semantic constraints differ;
- `REFINEMENT_STRUCTURAL_VALIDATION_FAILED` — candidate introduces unacceptable structural/ERC
  regression;
- `REFINEMENT_STALE`, `REFINEMENT_CANDIDATE_HASH_MISMATCH`, `REFINEMENT_TARGET_STALE` — source,
  candidate, or trusted wizard/job binding changed;
- `REFINEMENT_AMBIGUOUS_TARGET` / `REFINEMENT_PLAN_INVALID_REFERENCE` — deterministic target cannot
  be uniquely/validly resolved;
- `REFINEMENT_UNSUPPORTED_OPERATION` / `REFINEMENT_OPERATION_BUDGET_EXCEEDED` — planner exceeded the
  mutation vocabulary/policy;
- `REFINEMENT_DERIVED_STATE_REFRESH_FAILED` — canonical mutation/evidence may be committed but a
  required derived artifact refresh failed; inspect `authoritative_committed` and do not blindly
  replay.

## Safe production invocation

After a wizard session has successfully generated its current project, enable refinement through the
validated process environment and invoke only by wizard session ID:

```bash
export KICAD_WEBAPP_REFINEMENT_ENABLED=true
# Configure the normal application LLM provider/model and explicit vision capability.
kicad-refine --session-id wiz_20260811_120000_abcd1234
```

The session ID must identify the completed wizard session whose `latest_job_id`, persisted
`CircuitIR`, canonical job workspace, and generated schematic all pass the production ownership
checks. There is intentionally no `--path`, `--job-id`, provider/model override, API-key override,
or KiCad executable override.

For Phase N evaluation, use the documented `kicad-refine-eval` harness against the committed
evaluation-corpus manifest and explicit isolated output/work roots. Do not point evaluation output at
a user's active project/session.

## Troubleshooting

### Capability unavailable

Check validated LLM configuration: provider enabled, explicit model present, client configured, and
`vision_enabled=true`. Do not work around the gate by selecting a model name that merely *sounds*
vision capable or by sending text-only critique.

### Render failure

Treat render errors as hard input-quality failures. Confirm the schematic exists, KiCad can export
the requested sheet, page geometry is supported, and SVG/PNG dimensions remain within limits. Do
not use an older render as evidence for current schematic bytes.

### KiCad export / electrical verification failure

Confirm `kicad-cli >= 9.0.0`, inspect the sanitized machine-readable code, and reproduce the export on
the candidate artifact. Do not convert export/parser failure into `not_run` or assume the candidate
is equivalent.

### Electrical mismatch

Reject the candidate. Inspect the electrical mismatch/evidence to identify component inventory,
terminal partition, footprint, no-connect, or helper-symbol differences. Fix the deterministic
operation/implementation; do not weaken the equivalence gate for the model's proposed layout.

### No meaningful improvement

`REFINEMENT_STOP_NO_MEANINGFUL_IMPROVEMENT` is a normal bounded outcome. Review deterministic metric
deltas and human evidence. A lack of measurable improvement is not a reason to force promotion.

### Oscillation

`REFINEMENT_STOP_OSCILLATION` means the loop encountered a previously seen layout fingerprint. The
best-known accepted schematic is retained. Investigate critic/planner guidance or operation
vocabulary before increasing bounds; do not disable fingerprint protection.

### Stale or ambiguous target

Regenerate a plan from current schematic/render context. Never fuzzy-match an unknown/stale object
or manually rewrite source hashes to force application.
