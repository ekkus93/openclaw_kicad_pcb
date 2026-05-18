"""S-expression parsing and serialization for KiCad file formats.

This sub-package implements a full tokenizer → parser → serializer pipeline
plus utility helpers for navigating and transforming the resulting AST.

Parser Strategy (single source of truth)
-----------------------------------------
The in-repo :mod:`.parser` / :mod:`.serializer` stack is the **sole** mechanism
used to read and write ``.kicad_sch`` / ``.kicad_pcb`` files in production code.
External libraries (e.g. ``kiutils``) are **not** imported by any runtime module;
they are retained only in the development / test environment as an optional
independent verification tool (see ``tests/unit/test_fixtures.py``).

This avoids dual-parser drift: there is exactly one code path that determines
how AST nodes are created, mutated, and serialized back to disk.

Quick Start
-----------
::

    from kicad_pcb.sexpr import parse, serialize, find_first, AtomNode

    root = parse('(kicad_sch (version 20230121) (generator eeschema))')
    print(root.key)           # → "kicad_sch"

    ver = find_first(root, "version")
    print(ver.items[1])       # → AtomNode("20230121", ...)

    text = serialize(root)    # round-trip back to string

Public symbols
--------------
From :mod:`.nodes`:
    ``Position``, ``NO_POS``, ``AtomNode``, ``StringNode``, ``ListNode``, ``Node``

From :mod:`.tokenizer`:
    ``Token``, ``TokenKind``, ``tokenize``

From :mod:`.parser`:
    ``parse``, ``parse_file``

From :mod:`.serializer`:
    ``serialize``, ``serialize_file``

From :mod:`.utils`:
    ``walk``, ``find_first``, ``find_all``,
    ``replace_section``, ``append_to_section``, ``node_path``

From :mod:`.builder`:
    ``atom``, ``string``, ``L``, ``fnum``, ``fnum_or_keep``
"""

from __future__ import annotations

from .builder import L, atom, fnum, fnum_or_keep, string
from .nodes import NO_POS, AtomNode, ListNode, Node, Position, StringNode
from .parser import parse, parse_file
from .serializer import serialize, serialize_file
from .tokenizer import Token, TokenKind, tokenize
from .utils import (
    append_to_section,
    find_all,
    find_first,
    node_path,
    replace_section,
    walk,
)

__all__ = [
    # nodes
    "Position",
    "NO_POS",
    "AtomNode",
    "StringNode",
    "ListNode",
    "Node",
    # tokenizer
    "Token",
    "TokenKind",
    "tokenize",
    # parser
    "parse",
    "parse_file",
    # serializer
    "serialize",
    "serialize_file",
    # utils
    "walk",
    "find_first",
    "find_all",
    "replace_section",
    "append_to_section",
    "node_path",
    # builder
    "atom",
    "string",
    "L",
    "fnum",
    "fnum_or_keep",
]
