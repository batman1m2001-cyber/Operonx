"""Typed edits to a graph's Python source.

The same rule as the config editors, for the same reason: **locate with the
AST, splice the text**. Never parse-and-unparse. ``ast.unparse`` produces
valid Python and destroys everything a reader put there — comments,
alignment, string quoting, blank lines — so a UI that "moved a node" would
hand back a file its author no longer recognises.

The AST is used only for what text alone cannot answer: *which* `agent` in
`agent.llm_resource` is the local variable and which is an attribute, where
an op's construction call ends, whether a name is a reference or part of a
string. Position ranges come from the AST; every byte outside them survives.

Contract, identical to ``edit``:

    **An edit that changes nothing returns the input byte for byte.**

Edits are applied back to front so that earlier offsets stay valid, and each
function edits within one ``@graph`` body so a name in a different graph is
never touched.
"""

from __future__ import annotations

import ast
from typing import Any, List, Tuple

__all__ = [
    "PyEditError",
    "set_op_resource",
    "rename_op",
    "find_graph",
    "graph_names",
    "op_names",
    "delete_op",
    "insert_op_after",
    "insert_op_between",
]


class PyEditError(Exception):
    """The requested edit does not apply to this source."""


def _line_offsets(text: str) -> List[int]:
    offsets = [0]
    for line in text.splitlines(keepends=True):
        offsets.append(offsets[-1] + len(line))
    return offsets


def _span(node: ast.AST, offsets: List[int]) -> Tuple[int, int]:
    """Absolute (start, end) byte offsets for a node with position info."""
    end_lineno = getattr(node, "end_lineno", None)
    end_col = getattr(node, "end_col_offset", None)
    if end_lineno is None or end_col is None:  # pragma: no cover
        raise PyEditError("source lacks end positions; Python 3.8+ required")
    return (
        offsets[node.lineno - 1] + node.col_offset,
        offsets[end_lineno - 1] + end_col,
    )


def _apply(text: str, edits: List[Tuple[int, int, str]]) -> str:
    """Splice replacements in, back to front. No change → same object."""
    live = [(s, e, r) for s, e, r in edits if text[s:e] != r]
    if not live:
        return text
    out = text
    for start, end, replacement in sorted(live, reverse=True):
        out = out[:start] + replacement + out[end:]
    return out


def _is_graph(node: ast.AST) -> bool:
    for decorator in getattr(node, "decorator_list", []):
        target = decorator.func if isinstance(decorator, ast.Call) else decorator
        if isinstance(target, ast.Name) and target.id == "graph":
            return True
    return False


def find_graph(tree: ast.Module, name: str) -> ast.AST:
    """The ``@graph`` body identified by *name*.

    *name* may be the ``@graph`` function itself, or a **builder** that
    wraps one. A manifest entry names the builder
    (``callbot.graph:build_ws_callbot_pipeline``) while the body belongs to
    the ``@graph`` inside it (``ws_callbot_pipeline``), so an edit API driven
    by the manifest would otherwise find nothing in exactly the projects
    that use the pattern.

    Nested graphs count either way: restricting this to module scope would
    miss every graph callbot has.
    """
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            if _is_graph(node):
                return node
            # A builder: take the @graph it defines. First one wins rather
            # than merging, so an edit never lands in a blend of two bodies.
            for inner in ast.walk(node):
                if (
                    isinstance(inner, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and inner is not node
                    and _is_graph(inner)
                ):
                    return inner
    raise PyEditError(f"no @graph named {name!r}")


def _parse(text: str) -> ast.Module:
    try:
        return ast.parse(text)
    except SyntaxError as exc:
        raise PyEditError(f"cannot parse source: {exc}") from exc


def _assignment_for(body: ast.AST, op_name: str) -> ast.Assign:
    """The single-target assignment that creates *op_name*.

    C3 guarantees an op's name is its assignment variable, which is what
    makes a node in the UI addressable in the source at all.
    """
    for node in ast.walk(body):
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name) and target.id == op_name:
                return node
    raise PyEditError(f"no assignment named {op_name!r} in this graph")


def _render_literal(value: Any, quote: str = "'") -> str:
    """Render a Python literal, matching the file's existing quote style.

    ``repr`` always single-quotes, so re-emitting an unchanged
    ``resource="gpt-4o"`` as ``'gpt-4o'`` would rewrite a line that did not
    change — breaking the byte-identical contract and leaving a file with
    two quoting styles.
    """
    if isinstance(value, str):
        if quote == '"' and '"' not in value and "\\" not in value:
            return f'"{value}"'
        return repr(value)
    if isinstance(value, (bool, int, float)) or value is None:
        return repr(value)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_render_literal(v, quote) for v in value) + "]"
    raise PyEditError(f"cannot render {type(value).__name__} as a literal")


def set_op_resource(text: str, graph: str, op_name: str, resource: Any) -> str:
    """Repoint one op at a different resource.

    Only the ``resource=`` value is replaced. Every other argument, the
    call's line breaks and any comment between arguments are untouched, so
    the diff is the one token that changed.

    Raises:
        PyEditError: the graph, the op, or a literal ``resource=`` argument
            is absent. A dynamic value such as ``resource=agent.llm_resource``
            is deliberately refused — rewriting it would silently sever the
            injection the author chose (see C6).
    """
    tree = _parse(text)
    offsets = _line_offsets(text)
    assign = _assignment_for(find_graph(tree, graph), op_name)
    if not isinstance(assign.value, ast.Call):
        raise PyEditError(f"{op_name!r} is not built by a call")

    for keyword in assign.value.keywords:
        if keyword.arg != "resource":
            continue
        if not isinstance(keyword.value, (ast.Constant, ast.List, ast.Tuple)):
            raise PyEditError(
                f"{op_name}.resource is computed, not a literal — repointing it here "
                f"would sever the value that supplies it"
            )
        start, end = _span(keyword.value, offsets)
        existing = text[start:end]
        quote = '"' if existing[:1] == '"' or '"' in existing else "'"
        return _apply(text, [(start, end, _render_literal(resource, quote))])

    raise PyEditError(f"{op_name!r} has no resource= argument")


def rename_op(text: str, graph: str, old: str, new: str) -> str:
    """Rename an op and every reference to it, inside one graph body.

    Node identity is ``parent.full_name + "." + name`` and the name comes
    from the assignment variable, so renaming a node in the UI *is* renaming
    the variable. References are found as ``ast.Name`` nodes, which is what
    keeps a matching word inside a string or an attribute out of the edit.

    Raises:
        PyEditError: *new* is not an identifier, or is already used in this
            graph — a collision would make one op silently overwrite the
            other at build time (``graph_op.py:201``).
    """
    if not new.isidentifier():
        raise PyEditError(f"{new!r} is not a valid Python name")
    tree = _parse(text)
    body = find_graph(tree, graph)
    offsets = _line_offsets(text)

    names = [n for n in ast.walk(body) if isinstance(n, ast.Name)]
    if not any(n.id == old for n in names):
        raise PyEditError(f"no name {old!r} in @graph {graph}")
    if old != new and any(n.id == new for n in names):
        raise PyEditError(
            f"{new!r} is already used in @graph {graph}; two ops sharing a name "
            f"means the second silently overwrites the first"
        )

    edits = [(*_span(n, offsets), new) for n in names if n.id == old]
    return _apply(text, edits)


def _each_graph(tree: ast.Module) -> List[ast.AST]:
    return [
        n
        for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and _is_graph(n)
    ]


def graph_names(text: str) -> List[str]:
    """Names of every ``@graph`` in the source, nested ones included."""
    return [g.name for g in _each_graph(_parse(text))]


def op_names(text: str, graph: str) -> List[str]:
    """Assignment targets inside one graph body, in source order."""
    body = find_graph(_parse(text), graph)
    found: List[str] = []
    for node in ast.walk(body):
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name) and target.id not in found:
                found.append(target.id)
    return found


# ── topology: chains of ``>>`` ───────────────────────────────────────────


def _flatten_rshift(node: ast.AST) -> List[ast.AST]:
    """Operands of a ``a >> b >> c`` chain, left to right.

    ``>>`` is left-associative, so the parse is ``((a >> b) >> c)`` — a
    linked list leaning left rather than a flat sequence.
    """
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.RShift):
        return _flatten_rshift(node.left) + _flatten_rshift(node.right)
    return [node]


def _chains(body: ast.AST) -> List[ast.BinOp]:
    """Every top-level ``>>`` expression statement in a graph body."""
    out = []
    for node in ast.walk(body):
        if (
            isinstance(node, ast.Expr)
            and isinstance(node.value, ast.BinOp)
            and isinstance(node.value.op, ast.RShift)
        ):
            out.append(node.value)
    return out


def _names_in(node: ast.AST) -> List[str]:
    return [n.id for n in ast.walk(node) if isinstance(n, ast.Name)]


def _statement_lines(node: ast.AST, offsets: List[int], text: str) -> Tuple[int, int]:
    """The whole-line span of a statement, so removing it leaves no blank."""
    start = offsets[node.lineno - 1]
    end_lineno = getattr(node, "end_lineno", node.lineno)
    end = offsets[end_lineno] if end_lineno < len(offsets) - 1 else len(text)
    return start, end


def delete_op(text: str, graph: str, name: str) -> str:
    """Remove an op and take it out of every wiring chain.

    Refuses when another op still reads its output. Deleting a node that
    something depends on does not produce a smaller graph, it produces a
    broken one, and the honest answer is for the UI to say "disconnect it
    first" rather than to emit source that no longer builds.

    Raises:
        PyEditError: the op is absent, or another op references it.
    """
    tree = _parse(text)
    body = find_graph(tree, graph)
    offsets = _line_offsets(text)
    assign = _assignment_for(body, name)

    # A reference from another op's arguments — `q=pre["cleaned"]` — is a
    # dependency. A mention inside a `>>` chain is just wiring, which this
    # function rewrites rather than refuses.
    for other in ast.walk(body):
        if not isinstance(other, ast.Assign) or other is assign:
            continue
        if name in _names_in(other.value):
            raise PyEditError(
                f"{name!r} is still read by another op; disconnect it before deleting"
            )

    edits: List[Tuple[int, int, str]] = [(*_statement_lines(assign, offsets, text), "")]

    for statement in ast.walk(body):
        if not isinstance(statement, ast.Expr):
            continue
        value = statement.value
        if not (isinstance(value, ast.BinOp) and isinstance(value.op, ast.RShift)):
            continue
        operands = _flatten_rshift(value)
        kept = [o for o in operands if _names_in(o) != [name]]
        if len(kept) == len(operands):
            continue
        if len(kept) < 2:
            # Nothing left to connect — drop the whole statement rather than
            # leave a dangling `START >>`.
            edits.append((*_statement_lines(statement, offsets, text), ""))
            continue
        start, end = _span(value, offsets)
        rebuilt = " >> ".join(text[s:e] for s, e in (_span(o, offsets) for o in kept))
        edits.append((start, end, rebuilt))

    return _apply(text, edits)


def insert_op_after(text: str, graph: str, after: str, name: str, call: str) -> str:
    """Add ``name = call`` after *after*, and splice it into the chains.

    Splices into **every** chain that mentions *after*. When a node has two
    downstream paths that is rarely what the user meant — see
    :func:`insert_op_between` for the per-edge form, which is what a "+" on
    an edge should call.

    Raises:
        PyEditError: *name* is not a valid identifier, is already used in
            this graph, or *after* is absent.
    """
    if not name.isidentifier():
        raise PyEditError(f"{name!r} is not a valid Python name")
    tree = _parse(text)
    body = find_graph(tree, graph)
    offsets = _line_offsets(text)

    if name in {n.id for n in ast.walk(body) if isinstance(n, ast.Name)}:
        raise PyEditError(
            f"{name!r} is already used in @graph {graph}; two ops sharing a name "
            f"means the second silently overwrites the first"
        )
    anchor = _assignment_for(body, after)

    start, end = _statement_lines(anchor, offsets, text)
    indent = text[offsets[anchor.lineno - 1] : offsets[anchor.lineno - 1] + anchor.col_offset]
    edits: List[Tuple[int, int, str]] = [(end, end, f"{indent}{name} = {call}\n")]

    for chain in _chains(body):
        operands = _flatten_rshift(chain)
        if not any(_names_in(o) == [after] for o in operands):
            continue
        rebuilt_parts: List[str] = []
        for operand in operands:
            s, e = _span(operand, offsets)
            rebuilt_parts.append(text[s:e])
            if _names_in(operand) == [after]:
                rebuilt_parts.append(name)
        c_start, c_end = _span(chain, offsets)
        edits.append((c_start, c_end, " >> ".join(rebuilt_parts)))

    return _apply(text, edits)


def insert_op_between(text: str, graph: str, src: str, dst: str, name: str, call: str) -> str:
    """Insert ``name`` on the single edge ``src >> dst``.

    This is the "+" on an edge, and it is the precise form of the gesture.
    :func:`insert_op_after` splices into *every* chain mentioning the anchor,
    which is surprising the moment a node has two downstream paths — in
    ex06, inserting after ``a`` lands in the ``EmitOp`` branch as well as the
    main one. When the UI's user clicked one edge, only that edge should
    move.

    Raises:
        PyEditError: the name is invalid or taken, or ``src >> dst`` are not
            adjacent in any chain in this graph.
    """
    if not name.isidentifier():
        raise PyEditError(f"{name!r} is not a valid Python name")
    tree = _parse(text)
    body = find_graph(tree, graph)
    offsets = _line_offsets(text)

    if name in {n.id for n in ast.walk(body) if isinstance(n, ast.Name)}:
        raise PyEditError(f"{name!r} is already used in @graph {graph}")
    anchor = _assignment_for(body, src)

    edits: List[Tuple[int, int, str]] = []
    spliced = False
    for chain in _chains(body):
        operands = _flatten_rshift(chain)
        parts: List[str] = []
        hit = False
        for index, operand in enumerate(operands):
            s, e = _span(operand, offsets)
            parts.append(text[s:e])
            following = operands[index + 1] if index + 1 < len(operands) else None
            if (
                _names_in(operand) == [src]
                and following is not None
                and _names_in(following) == [dst]
            ):
                parts.append(name)
                hit = True
        if not hit:
            continue
        spliced = True
        c_start, c_end = _span(chain, offsets)
        edits.append((c_start, c_end, " >> ".join(parts)))

    if not spliced:
        raise PyEditError(f"{src!r} >> {dst!r} is not an edge in @graph {graph}")

    _, end = _statement_lines(anchor, offsets, text)
    indent = text[offsets[anchor.lineno - 1] : offsets[anchor.lineno - 1] + anchor.col_offset]
    edits.append((end, end, f"{indent}{name} = {call}\n"))
    return _apply(text, edits)
