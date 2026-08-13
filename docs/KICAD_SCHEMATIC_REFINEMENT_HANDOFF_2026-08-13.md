# KiCad schematic refinement handoff — 2026-08-13

This file is the restart point for continuing the KiCad schematic electrical-invariance / AI visual-refinement work on the `webapp` branch.

It records the repository state immediately before stopping work on 2026-08-13, the work completed in the current iteration, the evidence that exists, the evidence that does **not** yet exist, the remaining work, and the order in which to resume.

## 1. Repository and branch

Repository:

- `ekkus93/openclaw_kicad_pcb`

Target branch:

- `webapp`

Code-state SHA immediately before this handoff document was committed:

- `c14c4b10eefd58c08b33b2ca81bd7a3feff578e8`
- commit message: `test: simplify Phase C fixture helpers`

The handoff-document commit is expected to be the direct successor of that SHA. On restart, always verify the actual `webapp` head before making changes because the branch may have advanced after this file was written.

Do not work on `master` unless explicitly instructed. The active schematic-refinement work described here is on `webapp`.

## 2. Working conventions for the next session

The user is monitoring GitHub Actions. Do **not** spend time polling or monitoring replacement CI runs unless the user explicitly asks. When the user provides a failing CI log, use that exact failure as the source of truth, fix it, push the correction to `webapp`, verify the diff/scope, and stop monitoring.

Direct GitHub network access from the sandbox has previously failed DNS resolution. The GitHub connector has been reliable for repository reads/writes and should be used when a normal checkout is not available. A user-supplied branch ZIP may also be used as a local sandbox source tree, but relevant files must be compared to current `webapp` before publishing so stale ZIP content cannot overwrite newer branch changes.

The local sandbox used during this work did not have a usable project Ruff/mypy toolchain. `uv` attempted to obtain Python 3.11 over blocked DNS. Therefore:

- do not claim a local Ruff or mypy pass unless the command actually ran successfully;
- syntax/pytest checks performed locally are useful but are not replacements for permanent CI;
- real `kicad-cli` integration evidence must come from an environment that actually has KiCad installed.

When a connector write is blocked by the OpenAI connector safety layer, the branch usually remains unchanged. Retrying an identical, already-verified write has succeeded. Always verify branch head and commit diff after any such retry.

## 3. Primary project documents

Read these first when resuming:

1. `docs/KICAD_SCHEMATIC_ELECTRICAL_INVARIANCE_AI_VISUAL_REFINEMENT_SPEC_2026-08-10.md`
2. `docs/KICAD_SCHEMATIC_ELECTRICAL_INVARIANCE_AI_VISUAL_REFINEMENT_TODO_2026-08-10.md`
3. `docs/KICAD_SCHEMATIC_REFINEMENT_LMN_IMPLEMENTATION_STATUS_2026-08-11.md`
4. `docs/KICAD_SCHEMATIC_REFINEMENT_HTTP_CLI_ADVERSARIAL_PRODUCTION_BOUNDARY_AUDIT_2026-08-13.md`
5. this handoff document

Important distinction: the supplemental “Phase N” in the LMN implementation-status document is feature/config/API/CLI integration work and is **not** the same thing as main-TODO Phase N, which is the experimental evaluation corpus.

## 4. Main TODO reconciliation state

The detailed refinement TODO was reconciled against implementation evidence in commit:

- `6064bbbc8df23c6bdab740373897f83c0360d8b7`
- `docs: reconcile schematic refinement TODO`

The reconciled TODO blob was:

- `8d32709cf37f0d4133a1972b404aa60193fbe503`

At that reconciliation point the checklist contained:

- 491 checked items
- 136 open items

Those counts are still the file’s recorded checkbox state as of this handoff because the A9 and Phase C work done afterward has intentionally **not** been marked complete without confirmed real-KiCad CI evidence.

Do not mechanically mark a phase done because code exists. The reconciliation rule is: an item is checked only when its implementation and required evidence are both present.

## 5. Major completed production-boundary hardening

The refinement HTTP/CLI production-boundary hardening commit is:

- `c651138c69aea77fcdb8f594e893ee938c269ffd`
- `fix: harden refinement production boundaries`

It addressed the following production boundaries:

### A1 — HTTP trusted-resolver detail leakage

The route catches controlled application errors and returns a fixed generic message plus controlled status/machine code rather than leaking trusted resolver details.

### A2 — parent-like wizard session IDs

Wizard session IDs reject `.`, `..`, and consecutive-dot parent-like syntax.

### A3 — wizard path aliases and persisted identity

Canonical wizard session storage is enforced, symlink/path aliases are rejected, and persisted session identity must match the requested session.

### A4 — cross-session identical-IR job substitution

Wizard-created jobs carry a private owner marker. Public job detail strips that marker. Refinement resolution requires the exact owner marker and legacy unowned jobs fail closed.

### A5 — CLI exception disclosure and cleanup override

Unexpected CLI errors become the generic `REFINEMENT_CLI_INTERNAL_ERROR`; cleanup failures are warning-visible and cannot replace/override the primary result.

The associated audit document is:

- `docs/KICAD_SCHEMATIC_REFINEMENT_HTTP_CLI_ADVERSARIAL_PRODUCTION_BOUNDARY_AUDIT_2026-08-13.md`

Local evidence recorded for that work included focused and broader web slices, including a 327-passed / 6-skipped available web sweep. Ruff/mypy/permanent CI were not claimed locally.

## 6. Trusted refinement production composition

Both the HTTP refinement route and the CLI session path use the trusted composed refinement pipeline rather than arbitrary user paths:

- HTTP: `/api/refinement/run`
- CLI: `kicad-refine --session-id <wizard-session-id>`

Production composition routes through:

`run_wizard_refinement_request()` → `run_configured_refinement_request()` → `run_configured_refinement()` → `refine_schematic()`

The trusted resolver requires, among other things:

- completed wizard session with valid current CircuitIR;
- latest generation job succeeded;
- generation request IR matches current wizard IR;
- canonical input CircuitIR validates and matches the wizard IR;
- persisted schematic path is relative and resolves inside the canonical job workspace;
- schematic has `.kicad_sch` suffix and exists;
- trusted paths are derived from persisted job metadata, not user path overrides;
- work/evidence roots are process-derived;
- wizard mutation is protected by the cross-process session lock;
- vision capability is explicit rather than inferred from a model name;
- there is no text-only fallback for vision refinement;
- canonical schematic, preview/ZIP, and job metadata remain synchronized;
- ZIP staging is atomic and symlink escapes are rejected;
- CLI accepts a session ID rather than arbitrary runtime/model/path/policy/bounds overrides.

Preserve these fail-closed boundaries when changing refinement code. Do not weaken them merely to make a test pass.

## 7. Root-instance schematic correctness work

Earlier root-instance correctness was fixed in:

- `1e9d0be866006f15360f113f6707eb8001f98d9f`
- `fix: emit root-qualified symbol instance paths`

Key behavior:

- newly inserted symbols in rooted schematics use `/<root-uuid>`;
- managed schematics inherit the qualified parent hierarchy;
- minimal/legacy documents without a root UUID preserve bare insertion behavior;
- invalid/missing root UUID handling fails explicitly where required;
- managed path updating accepts `/`, `/<root_uuid>`, and `/<root_uuid>/` forms.

A later Ruff PLR0915 refactor was:

- `adc1558de0bf03e44a0ba82ccb33b51f3921c8e3`
- `refactor: reduce generated schematic validation statements`

## 8. Recent Ruff/import/format repair history

After TODO reconciliation, permanent CI exposed several Ruff problems in files changed by the production-boundary work. They were repaired through a series of small style-only commits.

The important lesson is that Ruff’s import sorting uses `lint.isort.order-by-type = true` by default. Do not manually assume plain alphabetic ordering when resolving future I001 failures. Constants, CamelCase classes, and functions can sort in a different order than naive lexical sorting.

The final import-order repair before A9 work was:

- `620f52fcd1da3c18f20e1ed5cc94bfe25a83a710`
- `style: apply Ruff import ordering`

A wizard-persistence formatter correction followed:

- `7edfcfa4326748d0c365c1056bc1d0b825da337c`
- `style: format wizard persistence hardening test`

The user then reported CI green before substantive A9 work resumed.

## 9. Phase A9 — electrical-invariance fixture suite

### 9.1 Purpose

A9 is the real-KiCad electrical-invariance hard gate. Geometry-only mutations must remain electrically equivalent and deliberate semantic corruptions must fail closed.

The missing matrix identified by reconciliation was:

Positive cases:

- component move preserves electrical equivalence;
- component rotation preserves electrical equivalence;
- wire-bend-only mutation preserves electrical equivalence.

Negative cases:

- missing component;
- extra logical component;
- value change;
- pin moved to another net;
- net merge;
- net split;
- multi-unit corruption;
- no-connect removal/addition;
- helper-symbol classification cannot hide a real mismatch.

Older coverage already existed for symbol changes, footprint changes, unnamed-net renaming with the same partition, and unnamed-net partition corruption.

### 9.2 A9 implementation commit

The new A9 test suite was added in:

- `e355dc7b98d8391e49c3779465bf06c2ec050702`
- `test: expand electrical invariance A9 fixtures`

Files:

- `tests/integration/test_refinement_electrical_invariance.py`
- `tests/unit/test_refinement_electrical.py`

The suite added real-KiCad positive/negative fixtures plus unit/production-verifier coverage for multi-unit, no-connect, and adversarial helper classification.

Local evidence before real-KiCad execution:

- focused electrical/equivalence slice: 24 passed;
- integration module collected 10 cases;
- generated corruption candidates parsed successfully;
- no local `kicad-cli` was available.

### 9.3 First real-KiCad CI failure and root cause

The first real-KiCad run selected 33 integration tests; 29 passed and four new/affected refinement cases failed.

Three failures reported:

`UNNAMED_NET_PARTITIONS_MISMATCH`, with the candidate containing a singleton partition for `R3.2`.

Root cause: the base divider fixture left `R3.2` electrically unspecified, so real KiCad correctly exported it as a singleton unnamed net. The authoritative IR did not describe that floating terminal.

The fourth failure was the wire-bend positive fixture. The repository parser accepted a four-point `wire` node, but KiCad 9 refused to export the candidate schematic XML netlist.

### 9.4 A9 fixture repair

The A9 real-KiCad fixture repair was pushed in:

- `934c26fe286179f43cad1f192c0da200a0195f99`
- `test: repair A9 KiCad fixtures`

Changes included:

- explicitly connect `R3.2` to `GND`, so every resistor pin is represented in the authoritative IR;
- rebuild the dogleg as three legal two-point KiCad wire segments, each with a deterministic unique UUID;
- adjust semantic-corruption fixtures accordingly;
- strengthen rotation coverage so it is not intentionally specified as a trivial 0° operation.

Local evidence after repair:

- focused electrical unit suite: 24 passed;
- A9 integration module: 10 cases collected;
- semantic corruption fixtures construct and parse;
- dogleg is encoded as legal two-point wire segments;
- syntax/line-length checks passed.

### 9.5 A9 status at stop point

**Do not mark A9 or the Phase A hard gate complete yet.**

The user has not supplied a confirmed green real-KiCad integration result for the repaired A9 fixtures after `934c26fe...` in this conversation. If that evidence exists externally, verify it when resuming before checking the TODO boxes.

If the relevant integration CI is green on a descendant containing the A9 repair, update the A9 TODO items and Phase A exit gate accordingly. If CI reports another A9 failure, fix the exact fixture/verifier defect first and keep the gate open.

## 10. Phase C — deterministic layout-operation electrical invariance

### 10.1 Scope

Phase C’s deterministic operation primitives were already implemented. What reconciliation found missing was explicit electrical-invariance evidence for every registered low-risk layout operation:

- `move_component`
- `rotate_component`
- `move_label`
- `move_power_symbol`
- `align_components`
- `distribute_components`
- `move_component_group`

The Phase C exit gate requires the low-risk operation set to be deterministic, version-bound, and fully covered by electrical-invariance tests.

### 10.2 Phase C integration suite

A dedicated integration test file was added in:

- `aa9949300f903574f3dc67002605cb455764166f`
- `test: cover Phase C electrical invariance`

File:

- `tests/integration/test_refinement_phase_c_electrical_invariance.py`

The test path runs the production operation engine and then the production real-KiCad electrical verifier.

The suite covers all seven operations listed above.

Each test asserts that the candidate schematic hash actually changes before electrical verification so a nominal operation cannot satisfy the test by being a no-op.

Important discovery while building Phase C: in the generated divider, `R1` already starts at 90°. Therefore a hard-coded “rotate to 90°” positive would not exercise rotation. Phase C instead derives a different legal angle from the current component rotation.

For alignment/distribution, the suite uses an isolated named-net fixture with short label stubs collapsed onto pins. This allows components to be moved without synthetic wire-contact collisions while still making real KiCad export and verify the named electrical nets.

Local evidence before KiCad CI:

- focused refinement operation/electrical slice: 21 passed;
- Phase C integration module: 7 cases collected;
- all seven operation candidates constructed;
- all seven changed the schematic hash;
- all seven candidates reloaded through `SchematicDoc`;
- local `py_compile` passed;
- no local `kicad-cli` was available.

### 10.3 Ruff formatter failures on the new Phase C test file

Permanent CI then reported:

`uv run ruff format --check .`

with only:

- `tests/integration/test_refinement_phase_c_electrical_invariance.py`

requiring reformatting.

A first formatting-only correction was pushed:

- `96f73ca7a31a01cf73cceacfc6229d0edbc2bb37`
- `style: format Phase C invariance tests`

That collapsed three generator-expression layouts, but the user reported the same file still failed `ruff format --check` afterward. Therefore that correction was incomplete.

The local sandbox could not run Ruff 0.15.4 directly because the configured environment attempted network access for Python 3.11. Rather than continue guessing formatter wrapping, formatter-sensitive helper constructs were simplified.

### 10.4 Current Phase C head

The latest code-state commit is:

- `c14c4b10eefd58c08b33b2ca81bd7a3feff578e8`
- `test: simplify Phase C fixture helpers`

It changes only:

- `tests/integration/test_refinement_phase_c_electrical_invariance.py`

The change replaces several compact generator/`next(...)` lookups with straightforward helper/loop logic and explicit root-item rebuilding to reduce formatter-sensitive layout.

The commit was locally syntax-checked and the earlier focused operation/electrical tests remained the basis for the test logic. However, **there is no confirmed post-`c14c4b10...` Ruff CI result in this conversation.**

Therefore the very first CI-related question on restart is whether `ruff format --check` is green on a branch descendant containing `c14c4b10...`.

If the user reports another formatter failure for the same file, do not guess another small wrap manually. Obtain the exact formatter output if possible (for example by using a usable Ruff binary/tooling environment or a CI-produced diff), then apply the complete canonical format. Do not change functional behavior merely to appease formatting unless simplification is behavior-preserving and well tested.

### 10.5 Phase C status at stop point

**Do not mark Phase C or its exit gate complete yet.**

Two independent pieces of evidence are still required:

1. Ruff/static CI must accept the Phase C test file.
2. Real-KiCad integration CI must prove all seven operation candidates electrically invariant.

Once both are green on an exact SHA containing the Phase C suite, update the Phase C TODO checkboxes and exit gate.

## 11. Immediate resume sequence

Use this sequence when work resumes.

### Step 1 — verify branch state

Read current `webapp` head and compare it to this handoff’s baseline/code-state SHA.

Expected pre-handoff code state:

- `c14c4b10eefd58c08b33b2ca81bd7a3feff578e8`

The handoff commit should be its successor unless someone else advanced the branch.

Never overwrite newer branch work with a stale local ZIP.

### Step 2 — consume the user’s latest CI evidence

If the user reports a failure:

- fix exactly that failure;
- run the tightest available local regression slice;
- push to `webapp`;
- verify changed-file scope;
- do not monitor replacement CI.

If the user reports CI green:

- determine whether the green run includes both A9 real-KiCad coverage and the seven Phase C real-KiCad tests;
- only then mark those corresponding TODO items done.

### Step 3 — close A9 / Phase A if evidence supports it

Required result:

- geometry-only move passes real KiCad electrical verification;
- non-no-op rotation passes;
- legal wire-bend-only mutation passes;
- every intentional semantic corruption fails closed, including multi-unit/no-connect/helper-classification cases.

If all are proven on exact committed code, check the A9 items and the Phase A hard gate.

### Step 4 — close Phase C if evidence supports it

Required result:

All seven deterministic operations listed in section 10 must pass real-KiCad electrical-invariance integration testing, and the test file must be accepted by Ruff/static CI.

Then update the Phase C checkboxes and Phase C exit gate.

### Step 5 — start Phase D

Once A9/Phase A and Phase C are legitimately closed, the next substantive correctness task is:

**Phase D — electrical-invariance and real-KiCad integration coverage for bounded wire-geometry operations.**

The relevant implemented primitives are:

- `remove_redundant_wire_bend`
- `shorten_wire_path`
- `reroute_existing_net_orthogonal`

The remaining Phase D work is to prove these operations preserve electrical semantics, including real-KiCad export/verification where required, and then close the Phase D exit gate.

Reuse the A9/Phase C real-KiCad harness where possible instead of creating a third independent verification stack.

## 12. Remaining project work after Phase D

The reconciled TODO still has substantial work beyond A/C/D. The major open groups are below.

### F2 — large-schematic tiling/crops

Version 1 currently renders one deterministic whole sheet. There is no full multi-tile/crop system. Large-schematic/tiling items remain open or intentionally deferred. Do not mark F2 complete merely because whole-sheet rendering works.

### K — orchestration hard exit

Orchestration and failure-injection testing exists, but the hard exit still needs independently evidenced proof that one model-directed round improves a real fixture under the required real-KiCad path.

### L — cancellation

Most L bounds/best-known/cycle/stop behavior exists and has tests, but explicit user cancellation remains open.

### N — experimental evaluation corpus

Main-TODO Phase N1–N5 remains open. This is the experimental/evaluation corpus and should not be confused with the separate LMN implementation-status “Phase N” integration work.

### O — fallback/silent-failure audit

O1 trust-boundary work and required O3 fail-closed paths have substantial coverage. O2, the broader internal fallback and silent-failure audit, remains open. The Phase O exit gate therefore remains open.

### P — documentation/operator UX

Operator-facing documentation and UX completion remains open.

### Q — full acceptance matrix

Focused refinement/electrical/transaction/registry/metrics/critic/planner/loop/web-route tests have evidence, but the final acceptance phase still requires the broad gates called out in the TODO, including as applicable:

- full Python test suite;
- Ruff lint;
- Ruff format check;
- mypy;
- frontend checks if affected;
- browser smoke where required;
- real-KiCad integration suite;
- package smoke;
- coverage requirements.

Do not substitute focused local tests for these final acceptance gates.

### R — final closure

R1/R2/R3 are largely open. Reconciliation bookkeeping in R4 was partially completed, but final scope audit, exact-SHA evidence, final documentation, and final CI/completion evidence remain to be closed.

The project-wide definition of done is **not** yet satisfied.

## 13. Known deliberate deferrals from prior wizard hardening

Do not accidentally reintroduce these as if they were already required for the current refinement loop. Prior wizard/LLM production hardening deliberately deferred several larger architectural items, including:

- aggregate monotonic operation deadline semantics;
- out-of-lock/CAS provider execution;
- active cancellation/supersession;
- crash/restart recovery;
- full transition-framework centralization;
- frontend npm vulnerability remediation.

Some may overlap future refinement work (especially cancellation), but they were not silently completed by the current changes.

## 14. CI and validation claims that are safe to make

Historical evidence that was actually observed during this work includes:

- the user explicitly reported the CI jobs passing after the earlier wizard/Ruff repair chain and before A9 work;
- A9 initial real-KiCad integration run: 29 passed / 4 failed in the selected integration set, exposing the fixture defects described above;
- local A9 focused electrical slice after implementation/repair: 24 passed;
- local Phase C focused operation/electrical slice: 21 passed;
- A9 integration module collected 10 cases locally;
- Phase C integration module collected 7 cases locally;
- local syntax/parse/construction checks passed for the described candidates.

Claims that are **not** yet safe from this conversation:

- “A9 is green in real KiCad after the repair”;
- “Phase A exit gate is closed”;
- “Phase C is green in real KiCad”;
- “Phase C exit gate is closed”;
- “Ruff format passes on `c14c4b10...`”;
- “full Ruff/mypy/full Python/package/coverage/browser acceptance is green on the latest SHA.”

Require exact evidence before making any of those claims.

## 15. Important fixture/verifier principles discovered

Preserve these when extending Phase D or later integration tests.

### Represent every terminal intentionally

A CircuitIR fixture that omits a physically present symbol terminal can create real unnamed-net partitions in KiCad. The verifier is correct to report those. Test fixtures must model intentionally connected, named, floating, or no-connect terminals consistently.

### Use legal KiCad wire structure

Do not assume that a parser-accepted multi-point `wire` node is accepted by real KiCad export. The successful A9 repair represents a dogleg with separate legal two-point wire segments and unique UUIDs.

### Ensure positive operations are not no-ops

Hash the candidate before and after operation application and assert the hash changes. For rotations, derive a legal different angle from the current schematic state rather than hard-coding an angle that may already be active.

### Prefer production paths in integration tests

Use the production operation executor and production electrical verifier. Avoid test-only alternative semantics that could make a green test irrelevant to real behavior.

### Do not weaken fail-closed mismatch handling

If a semantic corruption unexpectedly passes, treat it as a verifier/fixture correctness problem. Do not broaden helper filtering, suppress mismatches, or special-case away electrical differences just to obtain a green integration test.

## 16. Recent commit chain most relevant to restart

In chronological order, the most useful recent SHAs are:

- `6064bbbc8df23c6bdab740373897f83c0360d8b7` — `docs: reconcile schematic refinement TODO`
- `620f52fcd1da3c18f20e1ed5cc94bfe25a83a710` — `style: apply Ruff import ordering`
- `7edfcfa4326748d0c365c1056bc1d0b825da337c` — `style: format wizard persistence hardening test`
- `e355dc7b98d8391e49c3779465bf06c2ec050702` — `test: expand electrical invariance A9 fixtures`
- `934c26fe286179f43cad1f192c0da200a0195f99` — `test: repair A9 KiCad fixtures`
- `aa9949300f903574f3dc67002605cb455764166f` — `test: cover Phase C electrical invariance`
- `96f73ca7a31a01cf73cceacfc6229d0edbc2bb37` — `style: format Phase C invariance tests`
- `c14c4b10eefd58c08b33b2ca81bd7a3feff578e8` — `test: simplify Phase C fixture helpers`

Also remember the earlier production-boundary commit:

- `c651138c69aea77fcdb8f594e893ee938c269ffd` — `fix: harden refinement production boundaries`

## 17. `memory.md` warning

Do not assume the repository’s `memory.md` is a complete source for this Aug-13 refinement work. During this session, local-only memory notes existed that were not pushed. This handoff document is the authoritative restart summary for the A9/Phase C state described here.

## 18. Recommended first substantive task after CI stabilization

If the latest CI is green and A9 + Phase C evidence is confirmed, the next Ralph Loop should be:

**“Phase D — real-KiCad electrical-invariance coverage for bounded wire-geometry operations.”**

The loop should:

1. inventory existing unit coverage for the three Phase D wire primitives;
2. reuse the A9 real-KiCad fixture/verifier harness;
3. construct legal KiCad wire geometry, not parser-only shapes;
4. assert each operation actually changes geometry;
5. verify electrical equivalence with production verifier/KiCad export;
6. add targeted negative tests where the operation must reject unsafe/unbounded topology changes;
7. run the tight local unit/integration slices possible in the sandbox;
8. push directly to `webapp`;
9. wait for user-provided CI evidence rather than monitoring Actions;
10. update Phase D TODO boxes only after required evidence is green.

## 19. Restart checklist

When a new session begins, use this compact checklist:

- [ ] Read this handoff.
- [ ] Verify current `webapp` head.
- [ ] Inspect any commits after the handoff before editing.
- [ ] Read the latest user-provided CI result.
- [ ] If CI failed, fix that exact failure first.
- [ ] If CI is green, verify whether A9 real-KiCad and all seven Phase C tests were included.
- [ ] Update A9/Phase A TODO only with confirmed evidence.
- [ ] Update Phase C TODO/exit gate only with confirmed evidence.
- [ ] Begin Phase D next.
- [ ] Keep remaining F2/K/L/N/O/P/Q/R gaps open until separately satisfied.
- [ ] Do not claim Ruff/mypy/full-suite/real-KiCad/package/coverage evidence that was not actually observed.
- [ ] Push completed work directly to `webapp` and verify exact diff scope.
- [ ] Do not monitor replacement CI unless explicitly asked.
