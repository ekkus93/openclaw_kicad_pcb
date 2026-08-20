# KiCad schematic refinement Phase O2 fallback audit

Date: 2026-08-20

This record closes the internal fallback/silent-failure audit required by Phase O2 of
`KICAD_SCHEMATIC_ELECTRICAL_INVARIANCE_AI_VISUAL_REFINEMENT_TODO_2026-08-10.md`.
The audit covers the refinement implementation and its production web/CLI composition. Its
acceptance criterion is stricter than ordinary availability: no fallback may convert an unknown,
unverified, stale, ambiguous, or electrically unsafe state into an accepted schematic.

## Audit method

The changed refinement surface was searched for broad exception handlers, warning-and-continue
paths, parse/validation defaults, `or []`/`or {}` defaults, provider/model capability fallback,
KiCad verification fallback, candidate-promotion fallback, fuzzy geometry resolution,
helper-symbol filtering, and metric failure-to-zero behavior. Each non-fatal path was then traced
to its caller and promotion gate, and existing regression coverage was checked. Missing direct
coverage for cleanup fallbacks was added in `tests/unit/test_refinement_o2_fallbacks.py`.

The audit found one implementation defect: `validate_candidate_structure()` created its temporary
ERC report path with `tempfile.mkstemp()` but did not close the returned descriptor. The descriptor
is now closed immediately, before the pathname is unlinked and before KiCad ERC runs. This was a
resource-leak defect, not a false-acceptance path, but repeated refinement could otherwise exhaust
process descriptors and destabilize verification.

## Intentional non-fatal fallback registry

| Area | Exact non-fatal failure | Behavior | Why it cannot create false success | Telemetry / regression evidence |
| --- | --- | --- | --- | --- |
| Electrical verification artifact cleanup | `OSError` while unlinking the temporary XML/native verification artifact | Keep the already-computed verification outcome; log WARNING | Candidate acceptance has already been decided from parsed KiCad exports and exact equivalence; deleting a private temporary file cannot change those bytes or the result | `_remove_verification_artifact()` WARNING; `test_verification_artifact_cleanup_failure_is_warning_visible` |
| ERC report cleanup | `OSError` while unlinking the private temporary ERC JSON after parsing | Return the already-computed structural report; log WARNING | ERC process success and report parsing/status are complete before cleanup | `validate_candidate_structure()` WARNING; `test_erc_report_cleanup_failure_is_warning_visible_and_does_not_mask_report` |
| Atomic-promotion parent-directory durability | `OSError` opening or fsyncing the parent directory after atomic replacement | Log WARNING; keep the exact promoted file already atomically replaced and hash-verified | Candidate file content is fsynced, atomic replacement has occurred, and promotion verifies expected accepted/candidate hashes; directory fsync affects crash-durability metadata, not in-process electrical/structural truth | `_fsync_directory()` WARNING; open/fsync regression tests in `test_refinement_o2_fallbacks.py` |
| Refinement render scratch cleanup | `OSError` removing a private temporary SVG/render directory after output publication | Keep validated rendered artifact; log WARNING | Published render files have already been generated and validated; scratch deletion does not alter the schematic or promotion gates | `render_schematic_for_refinement()` WARNING; `test_render_scratch_cleanup_failure_is_warning_visible_and_does_not_mask_success` |
| Evaluation CLI client cleanup | exception from `LlmClient.close()` after the command result is determined | Log WARNING | Client cleanup occurs after evaluation result production and cannot promote or modify an accepted schematic | `_close_llm_client()` WARNING; `test_evaluation_cli_client_cleanup_failure_is_warning_visible` |
| Optional public schematic preview refresh | known `PreviewGenerationError` | Remove any stale preview, record a sanitized preview warning, continue with the canonical accepted schematic | Preview PNG is explicitly derived/non-authoritative. The canonical `.kicad_sch` and refinement evidence are already committed; a stale preview is removed rather than misrepresented as current | runtime configuration contract; wizard refinement composition/preview non-fatal tests |
| Failure-path temporary directory cleanup | cleanup invoked with `ignore_errors=True` while handling an exception | Best-effort cleanup, then re-raise the original failure | These paths execute only after the primary operation has already failed; they do not return a successful result | evaluation/evidence failure-path tests and explicit re-raise in implementation |

Unexpected failures while regenerating the project ZIP or job metadata are *not* non-fatal
fallbacks. They raise `REFINEMENT_DERIVED_STATE_REFRESH_FAILED`, with
`authoritative_committed=true` when the canonical schematic/evidence may already be committed, so
callers cannot safely replay the mutation as if no commit occurred.

## Broad exception handlers

Broad handlers in the new refinement surface fall into three reviewed classes:

1. **Cleanup then re-raise.** Candidate/evidence/session/corpus temporary state is removed or
   rolled back and the original exception is re-raised.
2. **Sanitized process/interface conversion.** CLI, HTTP, and corpus boundaries convert an internal
   failure into a stable machine-readable failure without reflecting credentials, provider payloads,
   private paths, or raw model text. They do not convert it to success.
3. **Terminal failure bookkeeping.** The service attempts to publish failed session evidence and
   release/reserve state appropriately, while preserving the original hard failure as primary.

No reviewed `except Exception` branch returns an accepted refinement after an unknown verification
or mutation failure.

## Defaults and `or []` / `or {}` patterns

The reviewed defaults are optional-data normalization, not validation recovery. Examples include
empty optional artifact arrays, wizard assumptions, or pre-existing job-result metadata. Required
fields are still explicitly validated before refinement dispatch.

`{"status": "not_run"}` appears in iteration evidence for no-op/rejected paths where a later hard
gate genuinely was not executed. It is never accepted as a substitute for electrical or structural
verification on the promotion path. A candidate is promotable only after required electrical,
structural, geometry, quality, render/hash, and transaction checks succeed.

Metric counters are initialized to numeric zero as legitimate counts for empty geometry. The metric
implementation has no exception handler that converts parsing/computation failure into a zero or
successful metric report.

## Provider/model capability fallback

Production composition requires an enabled configured LLM provider, explicit model, configured
client, and `vision_enabled=true`. Missing capability fails with
`VISION_CAPABILITY_UNAVAILABLE` before model dispatch. Provider/model names are not parsed as
vision heuristics, and there is no text-only fallback that masquerades as a visual critic.

## KiCad verification fallback

Production electrical verification requires `kicad-cli >= 9.0.0`. Missing/old KiCad, XML/native
netlist export failure, malformed export, authoritative mismatch, footprint drift, or no-connect
drift fails closed. There is no production `not_run` or parser-error fallback that can be promoted.

Two narrowly scoped topology reconciliation rules were reviewed:

- XML-export symbol omissions may be backfilled only by **exact reference** from the candidate
  schematic, not by fuzzy identity.
- KiCad native unnamed nets omitted from XML may supplement topology only when an autogenerated
  native net has an **exact terminal partition** matching the authoritative unnamed net and XML has
  not already assigned those terminals. Explicitly named wrong nets and ambiguous partitions still
  fail equivalence.

## Candidate-promotion fallback

There is no candidate-promotion fallback. A planned candidate is isolated from accepted bytes and
must satisfy, in order, operation/source-hash validation, deterministic candidate metrics/hash,
electrical invariance, structural/ERC no-regression, hard geometry checks, deterministic quality
acceptance, post-edit render/hash checks, evidence publication, and transaction promotion. Promotion
rechecks the accepted baseline and candidate hashes and verifies the final promoted bytes.

No-op and rejected rounds assert that accepted bytes remain unchanged. Bounded-loop best-known
retention also verifies the final canonical hash before returning.

## Geometry target resolution

Unknown component references do not fuzzy-match. Geometry operations use stable IDs/anchors and
bounded exact/tolerance checks; zero or multiple logical matches produce
`REFINEMENT_AMBIGUOUS_TARGET` or stale-target failures. The implementation explicitly rejects
fuzzy geometry matching. Numeric values must be finite, bounded, and on the configured grid where
required.

## Helper-symbol filtering

Electrical comparison ignores only explicit KiCad power-helper symbols satisfying the complete
helper predicate (power-style reference/symbol identity plus non-BOM/non-board flags). A normal
component with a power-like reference is not hidden. Existing negative coverage verifies that such
a component still produces a component-inventory mismatch.

## Audit disposition

Phase O2 is accepted after the descriptor-leak repair and regression additions. The reviewed
fallbacks are either failure-path cleanup, sanitized error conversion, or warning-visible cleanup of
non-authoritative derived state. None bypasses electrical invariance, structural validation,
quality acceptance, stale/hash checks, or transactional promotion.

**Exit-gate conclusion:** no reviewed silent fallback converts an unsafe or unknown state into an
accepted schematic.
