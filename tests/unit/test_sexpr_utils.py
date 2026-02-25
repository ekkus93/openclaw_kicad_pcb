"""Tests for the S-expression utility helpers (kicad_pcb.sexpr.utils).

Phase 3.4 coverage:
- walk(): depth-first traversal visits all nodes
- find_first(): returns first direct child by keyword; None when missing
- find_all(): returns all direct children by keyword; empty list when none
- replace_section(): replaces matching section; appends when not found
- append_to_section(): appends item to matching section; raises KeyError otherwise
- node_path(): builds dot-notation path strings
- immutability: originals are never mutated by helpers
"""
from __future__ import annotations

import pytest
from kicad_pcb.sexpr.nodes import AtomNode, ListNode, StringNode
from kicad_pcb.sexpr.parser import parse
from kicad_pcb.sexpr.utils import (
    append_to_section,
    find_all,
    find_first,
    node_path,
    replace_section,
    walk,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_root() -> ListNode:
    """
    (kicad_sch
      (version 20230121)
      (generator eeschema)
      (symbol (lib_id "Device:R") (at 100 50))
      (symbol (lib_id "Device:C") (at 200 50)))
    """
    return parse(
        "(kicad_sch"
        "  (version 20230121)"
        '  (generator eeschema)'
        '  (symbol (lib_id "Device:R") (at 100 50))'
        '  (symbol (lib_id "Device:C") (at 200 50)))'
    )


# ---------------------------------------------------------------------------
# walk()
# ---------------------------------------------------------------------------


class TestWalk:
    def test_yields_root(self) -> None:
        root = _make_root()
        nodes = list(walk(root))
        assert nodes[0] is root

    def test_yields_all_nodes(self) -> None:
        root = _make_root()
        nodes = list(walk(root))
        # Should contain list nodes, atom nodes, string nodes
        list_nodes = [n for n in nodes if isinstance(n, ListNode)]
        atom_nodes = [n for n in nodes if isinstance(n, AtomNode)]
        assert len(list_nodes) >= 3
        assert len(atom_nodes) >= 5

    def test_atom_node(self) -> None:
        a = AtomNode("foo")
        nodes = list(walk(a))
        assert nodes == [a]

    def test_string_node(self) -> None:
        s = StringNode("bar")
        nodes = list(walk(s))
        assert nodes == [s]

    def test_depth_first_order(self) -> None:
        # root → first child → first grandchild → second child
        root = parse("(root (a (a1)) (b))")
        nodes = list(walk(root))
        # filter ListNodes
        lists = [n for n in nodes if isinstance(n, ListNode)]
        keys = [n.key for n in lists]
        assert keys == ["root", "a", "a1", "b"]

    def test_all_values_reachable(self) -> None:
        root = _make_root()
        atom_values = {n.value for n in walk(root) if isinstance(n, AtomNode)}
        assert "kicad_sch" in atom_values
        assert "version" in atom_values
        assert "symbol" in atom_values

    def test_empty_list(self) -> None:
        root = ListNode(())
        nodes = list(walk(root))
        assert nodes == [root]


# ---------------------------------------------------------------------------
# find_first()
# ---------------------------------------------------------------------------


class TestFindFirst:
    def test_finds_version(self) -> None:
        root = _make_root()
        ver = find_first(root, "version")
        assert ver is not None
        assert ver.key == "version"

    def test_finds_generator(self) -> None:
        root = _make_root()
        gen = find_first(root, "generator")
        assert gen is not None
        assert gen.items[1].value == "eeschema"  # type: ignore[union-attr]

    def test_returns_none_for_missing_key(self) -> None:
        root = _make_root()
        assert find_first(root, "nonexistent") is None

    def test_finds_first_of_multiple(self) -> None:
        root = _make_root()
        sym = find_first(root, "symbol")
        assert sym is not None
        # Should be the first symbol (Device:R), not the second
        lib_id = find_first(sym, "lib_id")
        assert lib_id is not None
        assert lib_id.items[1].value == "Device:R"  # type: ignore[union-attr]

    def test_does_not_recurse(self) -> None:
        # "lib_id" is inside symbol children, not a direct child of root
        root = _make_root()
        assert find_first(root, "lib_id") is None

    def test_atom_head_only(self) -> None:
        # A ListNode whose first item is a StringNode is not matched by key
        root = ListNode((
            ListNode((StringNode("not-an-atom"),)),
            ListNode((AtomNode("real-key"),)),
        ))
        assert find_first(root, "not-an-atom") is None
        assert find_first(root, "real-key") is not None


# ---------------------------------------------------------------------------
# find_all()
# ---------------------------------------------------------------------------


class TestFindAll:
    def test_finds_both_symbols(self) -> None:
        root = _make_root()
        syms = find_all(root, "symbol")
        assert len(syms) == 2

    def test_empty_list_when_missing(self) -> None:
        root = _make_root()
        assert find_all(root, "nonexistent") == []

    def test_single_result(self) -> None:
        root = _make_root()
        vers = find_all(root, "version")
        assert len(vers) == 1
        assert vers[0].key == "version"

    def test_does_not_recurse(self) -> None:
        root = _make_root()
        # "at" only exists inside symbols
        assert find_all(root, "at") == []

    def test_all_results_have_correct_key(self) -> None:
        root = _make_root()
        syms = find_all(root, "symbol")
        assert all(s.key == "symbol" for s in syms)


# ---------------------------------------------------------------------------
# replace_section()
# ---------------------------------------------------------------------------


class TestReplaceSection:
    def test_replaces_existing_version(self) -> None:
        root = _make_root()
        new_ver = ListNode((AtomNode("version"), AtomNode("99999")))
        new_root = replace_section(root, "version", new_ver)
        ver = find_first(new_root, "version")
        assert ver is not None
        assert ver.items[1].value == "99999"  # type: ignore[union-attr]

    def test_original_unchanged(self) -> None:
        root = _make_root()
        new_ver = ListNode((AtomNode("version"), AtomNode("99999")))
        _ = replace_section(root, "version", new_ver)
        ver = find_first(root, "version")
        assert ver is not None
        assert ver.items[1].value == "20230121"  # type: ignore[union-attr]

    def test_appends_when_not_found(self) -> None:
        root = _make_root()
        new_sec = ListNode((AtomNode("paper"), AtomNode("A4")))
        new_root = replace_section(root, "paper", new_sec)
        paper = find_first(new_root, "paper")
        assert paper is not None
        assert paper.items[1].value == "A4"  # type: ignore[union-attr]

    def test_replaces_only_first_matching(self) -> None:
        root = _make_root()
        new_sym = ListNode((AtomNode("symbol"), AtomNode("REPLACED")))
        new_root = replace_section(root, "symbol", new_sym)
        syms = find_all(new_root, "symbol")
        # First replaced, second untouched
        assert len(syms) == 2
        assert syms[0].items[1].value == "REPLACED"  # type: ignore[union-attr]

    def test_item_count_preserved_on_replace(self) -> None:
        root = _make_root()
        original_count = len(root.items)
        new_ver = ListNode((AtomNode("version"), AtomNode("2")))
        new_root = replace_section(root, "version", new_ver)
        assert len(new_root.items) == original_count

    def test_item_count_grows_on_append(self) -> None:
        root = _make_root()
        original_count = len(root.items)
        new_sec = ListNode((AtomNode("new_key"), AtomNode("val")))
        new_root = replace_section(root, "new_key", new_sec)
        assert len(new_root.items) == original_count + 1


# ---------------------------------------------------------------------------
# append_to_section()
# ---------------------------------------------------------------------------


class TestAppendToSection:
    def test_appends_to_existing_section(self) -> None:
        root = _make_root()
        # append an atom into root's (version ...) section
        new_root = append_to_section(root, "version", AtomNode("extra"))
        ver = find_first(new_root, "version")
        assert ver is not None
        assert len(ver.items) == 3  # (version 20230121 extra)

    def test_raises_key_error_when_missing(self) -> None:
        root = _make_root()
        with pytest.raises(KeyError, match="nonexistent"):
            append_to_section(root, "nonexistent", AtomNode("x"))

    def test_original_unchanged_after_append(self) -> None:
        root = _make_root()
        _ = append_to_section(root, "version", AtomNode("extra"))
        ver = find_first(root, "version")
        assert ver is not None
        assert len(ver.items) == 2  # original unchanged

    def test_appended_item_is_listnode(self) -> None:
        root = _make_root()
        # Append an atom into the (lib_id ...) sub-section of the first symbol
        sym = find_first(root, "symbol")
        assert sym is not None
        new_sym2 = append_to_section(sym, "lib_id", AtomNode("extra"))
        lib_id = find_first(new_sym2, "lib_id")
        assert lib_id is not None
        assert len(lib_id.items) == 3  # (lib_id "Device:R" extra)


# ---------------------------------------------------------------------------
# node_path()
# ---------------------------------------------------------------------------


class TestNodePath:
    def test_root_key_only(self) -> None:
        root = _make_root()
        assert node_path(root) == "kicad_sch"

    def test_one_step(self) -> None:
        root = _make_root()
        assert node_path(root, "version") == "kicad_sch.version"

    def test_multi_step(self) -> None:
        root = _make_root()
        path = node_path(root, "symbol", "lib_id")
        assert path == "kicad_sch.symbol.lib_id"

    def test_no_key_root(self) -> None:
        root = ListNode((StringNode("no-key"),))
        assert node_path(root) == "?"

    def test_no_key_with_steps(self) -> None:
        root = ListNode((StringNode("no-key"),))
        assert node_path(root, "child") == "?.child"
