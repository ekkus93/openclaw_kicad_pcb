# `lint.py` — Refactoring TODO

Cross-reference: code review in conversation history (2026-03-03).

Current state: 971 lines, three unrelated rule domains (SCH, LAY, PCB) plus shared
types, shared helpers, and a suggestions dictionary — all in one file.
Goal: ≤ ~60-line facade + four focused modules, all code smells fixed, missing tests added.

---

## Proposed target structure

```
kicad-pcb/src/kicad_pcb/
    lint_types.py      # LintSeverity, LintIssue, LintError, LINT_SUGGESTIONS   (~90 lines)
    lint_helpers.py    # shared private helpers (_is_numeric_atom, etc.)          (~80 lines)
    lint_sch.py        # lint_schematic + lint_schematic_layout + SCH/LAY rules  (~290 lines)
    lint_pcb.py        # lint_pcb + PCB-specific helpers (_edge_cuts_lines, …)   (~290 lines)
    lint.py            # thin facade: __all__ + re-exports only                   (~55 lines)
```

`lint.py` re-exports everything that the existing `__all__` list names
(plus `lint_schematic_layout` and `LINT_SUGGESTIONS`), so **no call site or
test import changes** are needed.

---

## Phase 1 — Extract `lint_types.py`

Move all public-facing types and the suggestions dictionary into their own module.
These have zero dependency on the S-expression AST and no dependency on each other.

### 1.1 Create `kicad-pcb/src/kicad_pcb/lint_types.py`

- [x] Move `LintSeverity` enum class.
- [x] Move `LintIssue` dataclass.
- [x] Move `LintError` exception class (depends on `KiCadError` from `.errors`).
- [x] Move `_ERR` and `_WARN` module-level aliases.
  - [x] Keep them in `lint_types.py` as private module-level constants — they are
    used in every rule function, so placing them here (and re-importing from helper
    and rule modules) avoids repetition.
- [x] Move `LINT_SUGGESTIONS: dict[str, str]` dictionary.
  - [x] It currently lives 600+ lines away from the rules it documents. Keeping it
    in `lint_types.py` co-locates all lint metadata in a single place.
- [x] Write a module docstring explaining that this module contains only types and
    suggestions — no AST traversal.
- [x] `__all__` in `lint_types.py`:
  ```python
  __all__ = [
      "LintError",
      "LintIssue",
      "LintSeverity",
      "LINT_SUGGESTIONS",
  ]
  ```

### 1.2 Update `lint.py`

- [x] Add import block:
  ```python
  from .lint_types import (
      LintError,
      LintIssue,
      LintSeverity,
      LINT_SUGGESTIONS,
      _ERR,
      _WARN,
  )
  ```
- [x] Remove the class bodies of `LintSeverity`, `LintIssue`, `LintError` from
    `lint.py`.
- [x] Remove `LINT_SUGGESTIONS` dict body from `lint.py`.
- [x] Remove `from .errors import KiCadError` from `lint.py` (now in
    `lint_types.py`).
- [x] Remove `from dataclasses import dataclass` and `from enum import Enum`
    from `lint.py` (no longer needed there).

### 1.3 Tests

- [x] Confirm `LintSeverity`, `LintIssue`, `LintError`, `LINT_SUGGESTIONS` all
    still importable from `kicad_pcb.lint` (re-exported by the facade).
- [x] Confirm that `TestLintTypes` test class passes without changes.
  `pytest tests/unit/test_lint.py` — 62/62 passed ✅

---

## Phase 2 — Extract `lint_helpers.py`

Move all shared private helpers used by both the SCH and PCB rule modules.
These helpers have no dependency on which domain (schematic vs PCB) is being checked.

### 2.1 Create `kicad-pcb/src/kicad_pcb/lint_helpers.py`

- [x] Move `_is_numeric_atom(node: object) -> bool`.
- [x] Move `_float_from_atom(node: object) -> float | None`.
- [x] Move `_collect_uuids(root: ListNode) -> list[str]`.
- [x] Move `_get_property_value(sym: ListNode, prop_name: str) -> str | None`.
  - [x] This is used only in `lint_schematic`; move it here rather than into
      `lint_sch.py` because it is a generic AST property accessor that could be
      reused if new rules are added.
- [x] Move `_symbol_lib_id(sym: ListNode) -> str | None`.
  - [x] Same rationale as `_get_property_value`.
- [x] **Add** `_check_duplicate_uuids(uuids: list[str], code: str) -> list[LintIssue]`
    (see Phase 5.1 — eliminates the duplicate SCH002/PCB002 logic).
- [x] **Add** `_collect_wire_segments(items: list) -> list[tuple[float, float, float, float]]`
    (see Phase 5.2 — eliminates the duplicate LAY002/LAY005 wire-extraction logic).
- [x] Write a module docstring explaining that all functions are private helpers
    used by both `lint_sch.py` and `lint_pcb.py`.
- [x] All functions remain `_`-prefixed.
- [x] Import only what is needed: `AtomNode`, `ListNode`, `StringNode` from
    `.sexpr.nodes`; `walk` from `.sexpr.utils`; `LintIssue`, `_ERR` from
    `.lint_types`.

### 2.2 Update `lint.py`

- [x] Add import block:
  ```python
  from .lint_helpers import (
      _check_duplicate_uuids,
      _collect_uuids,
      _collect_wire_segments,
      _float_from_atom,
      _get_property_value,
      _is_numeric_atom,
      _symbol_lib_id,
  )
  ```
- [x] Remove all moved helper bodies from `lint.py`.
- [x] Remove `from .sexpr.nodes import AtomNode, ListNode, StringNode` from
    `lint.py` if no longer directly used there after the move (verify with ruff).

### 2.3 Tests

- [x] Confirm: `_is_numeric_atom`, `_float_from_atom` resolved by existing tests
    — no changes expected (tests access them via `lint_schematic` / `lint_pcb`
    paths, not directly).

---

## Phase 3 — Extract `lint_sch.py`

Move all schematic and layout lint rules into their own module. These rules
share the same input type (`ListNode` rooted at `kicad_sch`) and have no
dependency on PCB types.

### 3.1 Create `kicad-pcb/src/kicad_pcb/lint_sch.py`

- [ ] Move all LAY rule constants from `lint.py`:
  - [ ] `_LAY_LABEL_MAX_COUNT: int = 3`
  - [ ] `_LAY_STUB_FRACTION_THRESHOLD: float = 0.60`
  - [ ] `_LAY_SYMBOL_HALF_SIZE_MM: float = 5.08`
  - [ ] `_LAY_PAGE_MAX_X: float = 297.0`
  - [ ] `_LAY_PAGE_MAX_Y: float = 210.0`
  - [ ] `_LAY_MAX_ISLANDS: int = 2`
  - [ ] `_WIRE_STUB_LEN_MM: float = 5.08`
- [ ] Move `lint_schematic(root: ListNode) -> list[LintIssue]`.
  - [ ] Remove the `# noqa: PLR0912, PLR0915` suppression — after applying Phase
      5 fixes (deduplication + extracting nested helpers), the function will be
      short enough to satisfy the linter naturally.
  - [ ] Verify the noqa is no longer needed; if still needed after Phase 5, add a
      note explaining why (but do not leave a bare suppression without justification).
- [ ] Move `lint_schematic_layout(root: ListNode) -> list[LintIssue]`.
  - [ ] Same noqa goal.
- [ ] Keep all functions `_`-prefixed where appropriate; `lint_schematic` and
    `lint_schematic_layout` are public.
- [ ] Write a module docstring explaining the SCH and LAY rule ranges, the
    distinction between structural (`lint_schematic`) and readability
    (`lint_schematic_layout`) checks, and when to call each.
- [ ] `__all__` in `lint_sch.py`:
  ```python
  __all__ = ["lint_schematic", "lint_schematic_layout"]
  ```

### 3.2 Update `lint.py`

- [ ] Add import block:
  ```python
  from .lint_sch import lint_schematic, lint_schematic_layout
  ```
- [ ] Remove all moved rule bodies and LAY constants from `lint.py`.
- [ ] Remove `import math` and `from collections import Counter` from `lint.py`
    if only needed by the SCH/LAY rules (verify with ruff).

### 3.3 Tests

- [ ] Confirm all `TestSCH*` classes in `test_lint.py` pass — they call
    `lint_schematic` which is still re-exported from `kicad_pcb.lint`.
- [ ] Confirm `lint_schematic_layout` is accessible from `kicad_pcb.lint` for
    any test that calls it directly.

---

## Phase 4 — Extract `lint_pcb.py`

Move all PCB lint rules and their PCB-specific private helpers into their own module.

### 4.1 Create `kicad-pcb/src/kicad_pcb/lint_pcb.py`

- [ ] Move the module-level constant `_COORD_MAX: float = 10_000.0`.
- [ ] Move PCB-specific private helpers:
  - [ ] `_edge_cuts_lines(root: ListNode) -> list[ListNode]`
  - [ ] `_gr_line_endpoints(line: ListNode) -> … | None`
  - [ ] `_has_any_edge_cuts(root: ListNode) -> bool`
- [ ] Move `lint_pcb(root: ListNode) -> list[LintIssue]`.
  - [ ] Remove the `# noqa: PLR0912, PLR0915` suppression — the deduplication
      fixes in Phase 5 should reduce branch count below the threshold.
  - [ ] Verify the noqa is no longer needed after Phase 5 fixes.
- [ ] Write a module docstring explaining the PCB rule range (PCB001–PCB011),
    and noting that the Edge.Cuts validity rules (PCB005–PCB008) form a logical
    sub-group.
- [ ] `__all__` in `lint_pcb.py`:
  ```python
  __all__ = ["lint_pcb"]
  ```

### 4.2 Update `lint.py`

- [ ] Add import block:
  ```python
  from .lint_pcb import lint_pcb
  ```
- [ ] Remove all moved PCB helpers and `lint_pcb` body from `lint.py`.
- [ ] Remove `_COORD_MAX` from `lint.py` — it is now in `lint_pcb.py`.

### 4.3 Tests

- [ ] Confirm all `TestPCB*` classes in `test_lint.py` pass.

---

## Phase 5 — Fix code smells

Individual targeted fixes that do not require a new module. Most of these should
be applied during the Phase 3 and 4 moves (apply immediately as you write each
new module, not as a separate pass).

### 5.1 Deduplicate UUID-checking logic (SCH002 / PCB002)

- [x] In `lint_helpers.py`, add:
  ```python
  def _check_duplicate_uuids(uuids: list[str], code: str) -> list[LintIssue]:
      """Return ERROR issues for every UUID that appears more than once."""
      issues: list[LintIssue] = []
      seen: set[str] = set()
      for u in uuids:
          if u in seen:
              issues.append(LintIssue(_ERR, code, f"Duplicate UUID '{u}'"))
          seen.add(u)
      return issues
  ```
- [ ] In `lint_sch.py`, replace the SCH002 loop with:
  ```python
  issues.extend(_check_duplicate_uuids(_collect_uuids(root), "SCH002"))
  ```
- [ ] In `lint_pcb.py`, replace the PCB002 loop with:
  ```python
  issues.extend(_check_duplicate_uuids(_collect_uuids(root), "PCB002"))
  ```

### 5.2 Deduplicate wire-segment extraction (LAY002 / LAY005)

The same `pts → xy[0] / xy[1]` extraction loop appears nearly identically in
both LAY002 and LAY005.

- [x] In `lint_helpers.py`, add:
  ```python
  def _collect_wire_segments(
      items: list,
  ) -> list[tuple[float, float, float, float]]:
      """Return ``(x1, y1, x2, y2)`` for every well-formed wire in *items*."""
      segs: list[tuple[float, float, float, float]] = []
      for node in items:
          if not isinstance(node, ListNode) or node.key != "wire":
              continue
          pts = find_first(node, "pts")
          if pts is None:
              continue
          xy_nodes = [
              n for n in pts.items[1:]
              if isinstance(n, ListNode) and n.key == "xy"
          ]
          if len(xy_nodes) < 2:
              continue
          try:
              x1 = float(xy_nodes[0].items[1].value)   # type: ignore[union-attr]
              y1 = float(xy_nodes[0].items[2].value)   # type: ignore[union-attr]
              x2 = float(xy_nodes[1].items[1].value)   # type: ignore[union-attr]
              y2 = float(xy_nodes[1].items[2].value)   # type: ignore[union-attr]
              segs.append((x1, y1, x2, y2))
          except (AttributeError, ValueError, IndexError):
              pass
      return segs
  ```
- [ ] In `lint_schematic_layout` (in `lint_sch.py`):
  - [ ] Replace the LAY002 wire loop with `_collect_wire_segments(items)` for
      computing `wire_lengths`.
  - [ ] Replace the LAY005 wire loop with `_collect_wire_segments(items)` for
      computing `wire_endpoints`.
- [ ] Reduces `lint_schematic_layout` by ~25 lines.

### 5.3 Lift nested function definitions out of rule functions

`_find` and `_union` are defined inside `lint_schematic_layout` for the
union-find (LAY005) algorithm, and `_round_pt` is defined inside `lint_pcb`
for the PCB006 dangling-endpoint check. None of these closures capture anything
from the outer scope.

- [ ] In `lint_sch.py`, define at module level (private):
  ```python
  def _uf_find(parent: list[int], i: int) -> int:
      """Path-compressing find for the union-find used in LAY005."""
      while parent[i] != i:
          parent[i] = parent[parent[i]]
          i = parent[i]
      return i

  def _uf_union(parent: list[int], a: int, b: int) -> None:
      ra, rb = _uf_find(parent, a), _uf_find(parent, b)
      if ra != rb:
          parent[ra] = rb
  ```
  - [ ] Update the LAY005 block to call `_uf_find(parent, i)` and
      `_uf_union(parent, a, b)` instead of the nested `_find` / `_union`.
- [ ] In `lint_pcb.py`, define at module level (private):
  ```python
  _Pt = tuple[float, float]

  def _round_pt(pt: _Pt) -> _Pt:
      return (round(pt[0], 3), round(pt[1], 3))
  ```
  - [ ] Remove the `_Pt` alias and `_round_pt` definition from inside `lint_pcb`.
  - [ ] Remove the `# type: ignore[misc]` comment that was silencing the nested
      function type annotation.

### 5.4 Replace `type: ignore[union-attr]` casts with `_float_from_atom`

In `lint_schematic_layout`, symbol positions are extracted with:
```python
sx = float(at_node.items[1].value)  # type: ignore[union-attr]
sy = float(at_node.items[2].value)  # type: ignore[union-attr]
```
The `_float_from_atom` helper already exists for exactly this purpose.

- [ ] Replace all six `float(node.items[N].value)  # type: ignore[union-attr]`
    casts in `lint_schematic_layout` with calls to `_float_from_atom`, guarding
    with `if x is None: continue`.
- [ ] After the fix, remove any remaining `# type: ignore[union-attr]` comments
    from `lint_sch.py`.

### 5.5 Move `_LABEL_KEYS` to module level in `lint_sch.py`

Currently `_LABEL_KEYS = {"label", "global_label", "hierarchical_label", "net_tie"}`
is defined inside `lint_schematic`, re-created on every call.

- [ ] Move it to module level in `lint_sch.py`:
  ```python
  _SCH_LABEL_KEYS: frozenset[str] = frozenset({
      "label", "global_label", "hierarchical_label", "net_tie"
  })
  ```
- [ ] Use `frozenset` (hashable, signals immutability).

### 5.6 Move `_GR_KEYS`/`coord_keys` to module level in `lint_pcb.py`

Similarly, `_GR_KEYS` and `coord_keys` are `set` literals constructed inside
`lint_pcb` on every call.

- [ ] Move both to module level as `frozenset`:
  ```python
  _PCB_GR_KEYS: frozenset[str] = frozenset({
      "gr_line", "gr_arc", "gr_rect", "gr_poly", "gr_curve"
  })
  _PCB_COORD_KEYS: frozenset[str] = frozenset({"at", "start", "end", "xy"})
  ```

---

## Phase 6 — Add missing tests

`test_lint.py` covers SCH001–SCH009 and PCB001–PCB011 but has no coverage for
SCH010 or any LAY rule.

### 6.1 Add `TestSCH010` in `test_lint.py`

SCH010 fires when a `label`, `global_label`, `hierarchical_label`, or `net_tie`
node is missing an `(at …)` child.

- [ ] `test_label_missing_at_is_error()` — label node with no `(at …)` child
    produces SCH010 ERROR.
- [ ] `test_global_label_missing_at_is_error()` — same for `global_label`.
- [ ] `test_label_with_at_is_clean()` — label with an `(at x y)` child
    produces no SCH010 issue.

### 6.2 Add `TestLAY001` in `test_lint.py`

- [ ] `test_label_appears_once_is_clean()` — single label, no LAY001.
- [ ] `test_label_at_threshold_is_clean()` — label appears exactly
    `_LAY_LABEL_MAX_COUNT` times, no warning.
- [ ] `test_label_exceeds_threshold_is_warning()` — label appears
    `_LAY_LABEL_MAX_COUNT + 1` times, produces LAY001 WARNING.
- [ ] `test_multiple_labels_independent()` — two distinct label names, each
    exceeding the threshold, produces two LAY001 warnings.

### 6.3 Add `TestLAY002` in `test_lint.py`

- [ ] `test_all_stub_wires_triggers_warning()` — schematic with only stub-length
    wires (≤ 5.08 mm) produces LAY002.
- [ ] `test_mostly_long_wires_is_clean()` — majority of wires are above stub
    length, no LAY002.
- [ ] `test_no_wires_is_clean()` — schematic with no wire nodes, no LAY002.

### 6.4 Add `TestLAY003` in `test_lint.py`

- [ ] `test_two_overlapping_symbols_is_warning()` — two symbols within
    `2 × _LAY_SYMBOL_HALF_SIZE_MM` of each other produce LAY003.
- [ ] `test_two_separated_symbols_is_clean()` — symbols far apart, no LAY003.
- [ ] `test_single_symbol_no_overlap()` — one symbol, no LAY003.

### 6.5 Add `TestLAY004` in `test_lint.py`

- [ ] `test_symbol_inside_a4_is_clean()` — symbol at (100, 100), no LAY004.
- [ ] `test_symbol_outside_x_bound_is_error()` — symbol at (300, 100),
    produces LAY004 ERROR.
- [ ] `test_symbol_outside_y_bound_is_error()` — symbol at (100, 220),
    produces LAY004 ERROR.
- [ ] `test_symbol_at_origin_is_clean()` — symbol at (0, 0), no LAY004.

### 6.6 Add `TestLAY005` in `test_lint.py`

- [ ] `test_connected_wires_single_island()` — all wires form one connected
    component, no LAY005.
- [ ] `test_two_islands_is_clean()` — exactly `_LAY_MAX_ISLANDS` isolated
    components, no LAY005.
- [ ] `test_three_islands_is_warning()` — three disconnected wire groups
    produce LAY005 WARNING.
- [ ] `test_no_wires_is_clean()` — no wire nodes at all, no LAY005.

---

## Phase 7 — Slim `lint.py` to a thin facade

After Phases 1–4, `lint.py` should contain only imports and `__all__`.

### 7.1 Target content of `lint.py` after refactor

```python
"""Structural lint rules — public facade.

All rule implementations live in:
  lint_types   — LintSeverity, LintIssue, LintError, LINT_SUGGESTIONS
  lint_helpers — shared private AST helpers
  lint_sch     — lint_schematic, lint_schematic_layout (SCH + LAY rules)
  lint_pcb     — lint_pcb (PCB rules)

Import from this module for backwards compatibility with existing call sites.
"""
from __future__ import annotations

from .lint_pcb import lint_pcb
from .lint_sch import lint_schematic, lint_schematic_layout
from .lint_types import LINT_SUGGESTIONS, LintError, LintIssue, LintSeverity

__all__ = [
    "LintError",
    "LintIssue",
    "LintSeverity",
    "LINT_SUGGESTIONS",
    "lint_pcb",
    "lint_schematic",
    "lint_schematic_layout",
]
```

### 7.2 Verify `__init__.py` and other importers are unaffected

- [ ] `kicad_pcb/__init__.py` imports `lint_pcb`, `lint_schematic`, `LintError`,
    `LintSeverity`, `LintIssue`, `LINT_SUGGESTIONS` from `.lint` — all still
    re-exported from the facade ✓
- [ ] `pipeline.py` imports `LintError`, `LintIssue`, `LintSeverity`,
    `lint_pcb`, `lint_schematic`, `lint_schematic_layout` from `.lint` — still
    present ✓
- [ ] `formatting.py` imports `LINT_SUGGESTIONS`, `LintSeverity` from `.lint` ✓
- [ ] `cli.py` imports `LINT_SUGGESTIONS`, `LintError`, `LintSeverity` from
    `.lint` ✓
- [ ] `results.py` imports `LintIssue` from `.lint` ✓
- [ ] Run `grep -rn "from .lint import\|from kicad_pcb.lint import" kicad-pcb/src/`
    after the refactor to confirm nothing was missed.

### 7.3 Target line budget

- [ ] `lint.py` facade: ≤ 30 lines.
- [ ] `lint_types.py`: ≤ 100 lines.
- [ ] `lint_helpers.py`: ≤ 100 lines.
- [ ] `lint_sch.py`: ≤ 300 lines.
- [ ] `lint_pcb.py`: ≤ 300 lines.

---

## Phase 8 — Run full checks and commit

- [ ] `ruff check kicad-pcb/src/ tests/` — zero warnings.
- [ ] `mypy kicad-pcb/src/kicad_pcb/` — zero errors across all source files.
  - [ ] Pay attention to `lint_helpers.py`: the `_collect_wire_segments` helper
      will carry the same `# type: ignore[union-attr]` on `.value` access — add
      a comment and make sure mypy is happy with the overall shape.
- [ ] `pytest tests/unit/ tests/integration/ -q` — all tests pass (including
    all new `TestLAY*` and `TestSCH010` test classes).
- [ ] Verify line counts meet Phase 7.3 targets.
- [ ] Commit with message:
  `refactor: split lint.py into lint_types / lint_helpers / lint_sch / lint_pcb`

---

## Implementation order

| Priority | Phase | Rationale |
|----------|-------|-----------|
| 1 | 1 — Extract `lint_types.py` | Zero AST dependency; safest first step |
| 2 | 2 — Extract `lint_helpers.py` | Needed by both lint_sch and lint_pcb; do before Phase 5 fixes |
| 3 | 5.1 — Deduplicate UUID logic | One-liner fix; apply during Phase 2 while writing the helper |
| 4 | 5.2 — Deduplicate wire extraction | Apply during Phase 2 while writing the helper |
| 5 | 3 — Extract `lint_sch.py` | Apply 5.3/5.4/5.5 inline during this move |
| 6 | 4 — Extract `lint_pcb.py` | Apply 5.3/5.6 inline during this move |
| 7 | 7 — Slim `lint.py` to facade | Clean up after all moves are done |
| 8 | 6 — Add missing tests | Add once the new module layout is stable |
| 9 | 8 — Full checks + commit | Always last |
