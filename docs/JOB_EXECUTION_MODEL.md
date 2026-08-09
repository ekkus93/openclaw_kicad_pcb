# Job Execution Model

## Job statuses

| Status | Meaning |
|--------|---------|
| `queued` | Job accepted, not yet started |
| `running` | Job execution is in progress |
| `succeeded` | Generation completed successfully |
| `failed` | Generation failed; the persisted job contains a structured error |
| `cancelled` | Job was cancelled before completion |

## Current execution model

Project generation is synchronous in the current web app. `POST /api/jobs/from-netlist`
creates the job workspace, transitions the job through `queued`/`running`, executes the
deterministic generation pipeline, persists the terminal state, and only then returns.
Wizard project generation uses the same job service.

A synchronous request is not considered successful merely because a job record was
created. If generation fails:

- the failed job remains persisted and inspectable;
- the response is non-2xx (`422`, `503`, or sanitized `500` according to failure class);
- the safe response includes the `job_id`;
- unexpected internal failures also carry the same non-secret `error_id` in the server
  log, persisted job error, and HTTP response.

This is intentional: HTTP success means the synchronous generation operation succeeded,
not merely that diagnostic state was recorded.

## Restart/interruption handling

The synchronous implementation cannot resume a process that died while a job was
`queued` or `running`. At application startup the server therefore reconciles persisted
non-terminal jobs from an earlier process and marks them terminal `failed` with the
stable `JOB_INTERRUPTED_BY_RESTART` code.

The server never silently leaves an orphaned `running` job for the frontend to poll
forever, and it never pretends an interrupted job succeeded.

## Canonical workspace paths

The job ID plus configured jobs directory define the authoritative workspace. Runtime
paths are derived from that canonical location when persisted state is read; arbitrary
absolute paths inside `job.json` are not trusted as new workspace roots. A path that
escapes the workspace is an invariant violation and fails closed rather than falling
back to an absolute server path.

## Frontend polling

`useJobQuery` polls `/api/jobs/:id` every 3 seconds while a job is `queued` or `running`.
Polling stops at a terminal status. With the current synchronous backend, most browser
requests observe a terminal state immediately; the polling behavior remains useful for
inspection and for a possible future worker model.

## Optional schematic preview

The schematic PNG preview is derived output, not the primary project artifact. Preview
readiness requires both `kicad-cli` and `rsvg-convert`. If core project generation
succeeds but preview generation fails, the job may still succeed with `project.zip` and
a structured `PREVIEW_GENERATION_SKIPPED` warning. This is an explicitly allowed,
visible degraded mode.

Non-preview generation failures remain fatal. Do not broaden the preview exception into
a general best-effort generation policy.

## Future async worker model

If generation later moves to a background worker, job ownership/leases and resumability
must be designed explicitly. Do not infer worker semantics from the current persisted
`queued`/`running` states. The existing frontend polling can support a worker, but the
backend interruption and ownership rules would need a separate design.
