"""Unit tests for kicad_pcb.lint — LAY and PCB lint rules."""

from __future__ import annotations

from kicad_pcb.lint import (
    LintIssue,
    LintSeverity,
    lint_schematic_layout,
)
from kicad_pcb.lint._sch_layout import (
    _LAY_LABEL_MAX_COUNT,
    _LAY_MAX_ISLANDS,
    _LAY_SYMBOL_HALF_SIZE_MM,
)
from kicad_pcb.sexpr import parse
from kicad_pcb.sexpr.nodes import ListNode

_ERR = LintSeverity.ERROR
_WARN = LintSeverity.WARNING


def _codes(issues: list[LintIssue]) -> list[str]:
    return [i.code for i in issues]


def _err_codes(issues: list[LintIssue]) -> list[str]:
    return [i.code for i in issues if i.severity == _ERR]


def _warn_codes(issues: list[LintIssue]) -> list[str]:
    return [i.code for i in issues if i.severity == _WARN]


def _sch(body: str = "") -> ListNode:
    """Parse a minimal kicad_sch document with optional *body* appended."""
    return parse(
        f"(kicad_sch (version 20230121) (generator test)\n"
        f"  (lib_symbols)\n"
        f"  {body}\n"
        f'  (sheet_instances (path "/" (page "1")))\n'
        f")"
    )


def _pcb(body: str = "") -> ListNode:
    """Parse a minimal kicad_pcb document with optional *body* appended."""
    return parse(f"(kicad_pcb (version 20230121) (generator test)\n  {body}\n)")


_SYM_TMPL = """\
(symbol (lib_id "{lib}") (at 50 76 0) (unit {unit}) (uuid "{uuid}")
  (property "Reference" "{ref}" (at 0 0 0))
  (property "Value" "{val}" (at 0 0 0))
)"""


def _sym(
    ref: str,
    uuid: str,
    lib: str = "Device:R",
    val: str = "10k",
    *,
    unit: int = 1,
) -> str:
    return _SYM_TMPL.format(lib=lib, uuid=uuid, ref=ref, val=val, unit=unit)


# ---------------------------------------------------------------------------
# LAY001 — net label name appearing too many times
# ---------------------------------------------------------------------------


class TestLAY001:
    def test_label_appears_once_is_clean(self) -> None:
        root = _sch('(label "BUS" (at 10 20 0))')
        assert "LAY001" not in _codes(lint_schematic_layout(root))

    def test_label_at_threshold_is_clean(self) -> None:
        # Exactly _LAY_LABEL_MAX_COUNT (=3) occurrences — no warning.
        labels = " ".join(f'(label "BUS" (at {i * 20} 0 0))' for i in range(_LAY_LABEL_MAX_COUNT))
        root = _sch(labels)
        assert "LAY001" not in _codes(lint_schematic_layout(root))

    def test_label_exceeds_threshold_is_warning(self) -> None:
        # _LAY_LABEL_MAX_COUNT + 1 (=4) occurrences → LAY001 WARNING.
        n = _LAY_LABEL_MAX_COUNT + 1
        labels = " ".join(f'(label "BUS" (at {i * 20} 0 0))' for i in range(n))
        root = _sch(labels)
        assert "LAY001" in _warn_codes(lint_schematic_layout(root))

    def test_multiple_labels_independent(self) -> None:
        # Two distinct label names, each exceeding the threshold → two LAY001s.
        n = _LAY_LABEL_MAX_COUNT + 1
        bus_labels = " ".join(f'(label "BUS" (at {i * 20} 0 0))' for i in range(n))
        vcc_labels = " ".join(f'(label "VCC" (at {i * 20} 50 0))' for i in range(n))
        root = _sch(bus_labels + " " + vcc_labels)
        warns = _warn_codes(lint_schematic_layout(root))
        assert warns.count("LAY001") == 2


# ---------------------------------------------------------------------------
# LAY002 — majority of wires are stub-length
# ---------------------------------------------------------------------------


def _wire(x1: float, y1: float, x2: float, y2: float) -> str:
    """Return a minimal wire S-expression string."""
    return f"(wire (pts (xy {x1} {y1}) (xy {x2} {y2})))"


class TestLAY002:
    def test_all_stub_wires_triggers_warning(self) -> None:
        # All wires are 5 mm (≤ 5.08 mm stub threshold) → > 60% stubs → LAY002.
        stubs = " ".join(_wire(i * 10, 0, i * 10 + 5, 0) for i in range(4))
        root = _sch(stubs)
        assert "LAY002" in _warn_codes(lint_schematic_layout(root))

    def test_mostly_long_wires_is_clean(self) -> None:
        # 1 stub wire + 4 long wires → 20% stubs (< 60%) → no LAY002.
        stub = _wire(0, 0, 5, 0)  # 5 mm ≤ 5.08
        long_wires = " ".join(_wire(i * 30, 50, i * 30 + 20, 50) for i in range(4))  # 20 mm each
        root = _sch(stub + " " + long_wires)
        assert "LAY002" not in _codes(lint_schematic_layout(root))

    def test_no_wires_is_clean(self) -> None:
        root = _sch()
        assert "LAY002" not in _codes(lint_schematic_layout(root))


# ---------------------------------------------------------------------------
# LAY003 — overlapping symbols (approximate bounding boxes)
# ---------------------------------------------------------------------------


def _sym_at(x: float, y: float) -> str:
    """Return a minimal symbol S-expression at the given position."""
    return f"(symbol (at {x} {y} 0))"


class TestLAY003:
    def test_two_overlapping_symbols_is_warning(self) -> None:
        # Symbols half a step apart in both axes — well within 2 * HALF_SIZE threshold.
        offset = _LAY_SYMBOL_HALF_SIZE_MM * 0.5
        root = _sch(_sym_at(50, 50) + " " + _sym_at(50 + offset, 50 + offset))
        assert "LAY003" in _warn_codes(lint_schematic_layout(root))

    def test_two_separated_symbols_is_clean(self) -> None:
        # Symbols 3 * HALF_SIZE apart in x — exceeds 2 * HALF_SIZE threshold → no LAY003.
        gap = _LAY_SYMBOL_HALF_SIZE_MM * 3
        root = _sch(_sym_at(50, 50) + " " + _sym_at(50 + gap, 50))
        assert "LAY003" not in _codes(lint_schematic_layout(root))

    def test_single_symbol_no_overlap(self) -> None:
        root = _sch(_sym_at(50, 50))
        assert "LAY003" not in _codes(lint_schematic_layout(root))


# ---------------------------------------------------------------------------
# LAY004 — symbol outside page bounds
# ---------------------------------------------------------------------------


class TestLAY004:
    def test_symbol_inside_bounds_is_clean(self) -> None:
        root = _sch(_sym_at(100, 100))
        assert "LAY004" not in _codes(lint_schematic_layout(root))

    def test_symbol_outside_x_bound_is_error(self) -> None:
        # x=450 > 420 → LAY004 ERROR.
        root = _sch(_sym_at(450, 100))
        assert "LAY004" in _err_codes(lint_schematic_layout(root))

    def test_symbol_outside_y_bound_is_error(self) -> None:
        # y=310 > 297 → LAY004 ERROR.
        root = _sch(_sym_at(100, 310))
        assert "LAY004" in _err_codes(lint_schematic_layout(root))

    def test_symbol_at_origin_is_clean(self) -> None:
        # (0, 0) is exactly on the boundary → clean.
        root = _sch(_sym_at(0, 0))
        assert "LAY004" not in _codes(lint_schematic_layout(root))


# ---------------------------------------------------------------------------
# LAY005 — too many disconnected wire islands (union-find)
# ---------------------------------------------------------------------------


class TestLAY005:
    def test_connected_wires_single_island(self) -> None:
        # Chain: (0,0)-(10,0)-(20,0) — all endpoints shared → 1 island → no LAY005.
        wires = _wire(0, 0, 10, 0) + " " + _wire(10, 0, 20, 0)
        root = _sch(wires)
        assert "LAY005" not in _codes(lint_schematic_layout(root))

    def test_two_islands_is_clean(self) -> None:
        # Exactly _LAY_MAX_ISLANDS disconnected groups — at threshold → no LAY005.
        groups = " ".join(
            _wire(i * 100, i * 100, i * 100 + 10, i * 100) for i in range(_LAY_MAX_ISLANDS)
        )
        root = _sch(groups)
        assert "LAY005" not in _codes(lint_schematic_layout(root))

    def test_three_islands_is_warning(self) -> None:
        # _LAY_MAX_ISLANDS + 1 disconnected groups → exceeds threshold → LAY005 WARNING.
        groups = " ".join(
            _wire(i * 100, i * 100, i * 100 + 10, i * 100) for i in range(_LAY_MAX_ISLANDS + 1)
        )
        root = _sch(groups)
        assert "LAY005" in _warn_codes(lint_schematic_layout(root))

    def test_no_wires_is_clean(self) -> None:
        root = _sch()
        assert "LAY005" not in _codes(lint_schematic_layout(root))


# ---------------------------------------------------------------------------
# PCB001 — invalid root node
# ---------------------------------------------------------------------------
