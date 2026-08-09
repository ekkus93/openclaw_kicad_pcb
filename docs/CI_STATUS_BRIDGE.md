# Web App CI Status Bridge

The `webapp` branch uses a ChatGPT-readable GitHub Actions status bridge so an implementation loop can discover the exact current CI run without relying on local `gh` access.

## Authoritative status

- Monitored workflow: `CI`
- Monitored branch: `webapp`
- Persistent status issue: `#2` — `CI Status: Web App — webapp`
- Publisher workflow: `.github/workflows/publish-webapp-ci-status.yml`
- Publisher location: repository default branch (`master`)

The publisher exists on `master` because GitHub `workflow_run` publishers must be present on the repository default branch. It observes `CI` runs whose `head_branch` is exactly `webapp`.

## What the issue contains

The issue is overwritten as the latest applicable run changes state. It contains:

- exact workflow name and workflow ID;
- exact run ID and run attempt;
- exact `webapp` head SHA;
- run status and conclusion;
- every available job ID and job conclusion;
- every available step and step conclusion;
- abnormal jobs and steps;
- timing metadata;
- artifact IDs and metadata after completion;
- a machine-readable JSON snapshot.

Raw job logs, environment variables, secrets, and artifact contents are not copied into the issue.

## Safety properties

The bridge is observational only. It does not change the existing `CI` workflow or its quality gates.

The publisher:

1. requests only `actions: read`, `contents: read`, and `issues: write`;
2. does not check out repository code;
3. does not execute code from the triggering `webapp` commit;
4. rejects workflow runs whose `head_branch` is not exactly `webapp` at both the workflow-job level and inside the publisher logic;
5. queries the latest run for the same workflow and `webapp` branch before updating the issue, so an older event cannot overwrite a newer run;
6. verifies the issue ownership marker before modifying issue `#2`;
7. paginates jobs and artifacts rather than assuming a single API page;
8. fails explicitly on malformed GitHub API data;
9. compacts successful step detail deterministically if the issue approaches the body-size limit while preserving job IDs, conclusions, and abnormal-step metadata.

## Ralph Loop usage

For every candidate commit:

1. Record the exact candidate SHA.
2. Read issue `#2`.
3. Require the issue's `workflow.head_sha` to equal the candidate SHA.
4. If CI is still running, use the issue's run and job IDs to inspect current state.
5. If CI fails, fetch the exact failed job log using its job ID and diagnose the first meaningful failure.
6. After a fix, repeat against the new candidate SHA.
7. Do not claim CI is green unless issue `#2` reports `completed` / `success` for the exact candidate SHA.

The issue is an indexing bridge. GitHub Actions remains the authoritative execution system, and the job log remains the detailed source for failure output.
