# Questions and Issues — Batch 4.4 Pre-Implementation

## Q1: Fix both the 4.3 spec and the 4.4 spec in the same commit?

### Observation

`docs/UIUX_IMPROVEMENTS4_3_SPEC.md` has the stale `-m kicad` command in a fenced `bash` block (Section 1 "Problem", line 43). That is the fix described in Batch 4.4 Task 1 — clear, one-line prose replacement.

However, `docs/UIUX_IMPROVEMENTS4_4_SPEC.md` itself also shows the stale command in a fenced `bash` block (Section 1 "Problem"):

```text
docs/UIUX_IMPROVEMENTS4_4_SPEC.md, Section 1 "Problem":
  `docs/UIUX_IMPROVEMENTS4_3_SPEC.md` still contains a fenced command block like:
  [bash block showing: uv run ... python -m pytest -m kicad]
```

If we only fix the 4.3 spec, the 4.4 spec will have the same issue and likely trigger a Batch 4.5 with an identical task.

Batch 4.4 Task 2 says to search active/current docs for stale runnable examples and fix any found. The 4.4 spec qualifies — it is an active current doc and it contains a runnable-looking bash block with the obsolete marker.

### Question

Should I fix both `docs/UIUX_IMPROVEMENTS4_3_SPEC.md` and the Problem section of `docs/UIUX_IMPROVEMENTS4_4_SPEC.md` in the same Batch 4.4 commit, so that the cascade ends here?

Or should I fix only the 4.3 spec as the task strictly describes, and let the review process determine whether a 4.5 is needed?
