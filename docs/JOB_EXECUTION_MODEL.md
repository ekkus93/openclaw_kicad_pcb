# Job Execution Model

## Job statuses

| Status | Meaning |
|--------|---------|
| `queued` | Job accepted, not yet started |
| `running` | Job in progress |
| `succeeded` | Generation completed successfully |
| `failed` | Generation failed; error payload contains details |
| `cancelled` | Job was cancelled before completion |

## When frontend polling occurs

`useJobQuery` polls the `/api/jobs/:id` endpoint every 3 seconds while the
job status is `queued` or `running`. Polling stops automatically when the
job reaches a terminal status (`succeeded`, `failed`, `cancelled`).

The `isFetching` flag from TanStack Query drives the "Checking for updates…"
indicator shown on the JobPage status card during active polling.

## Current backend execution model

Project generation (`POST /api/generate`) runs **synchronously** inside the
request handler. The API creates a job record, runs the full generation
pipeline, and updates the job status to `succeeded` or `failed` before
returning. The job therefore spends almost no time in `queued` or `running`
from the client's perspective.

As a result, frontend polling fires at most once or twice before the job
enters a terminal state.

## Future async worker model

If generation is moved to a background worker (e.g. Celery, ARQ, or a
thread pool), jobs would remain in `queued`/`running` for seconds or minutes.
The existing polling logic handles this transparently — no frontend changes
are needed. The 3-second interval and the `isFetching` indicator would then
become more visible to users.
