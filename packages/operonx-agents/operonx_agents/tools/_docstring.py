"""Read a function's summary and per-argument descriptions from its docstring.

Three styles are read, the ones Python code actually uses:

- Google: an ``Args:`` (or ``Arguments:`` / ``Parameters:``) section of
  ``name: text`` / ``name (type): text`` lines, continuation lines indented.
- NumPy: a ``Parameters`` heading underlined with dashes, then ``name : type``
  lines with the text indented below.
- Sphinx: ``:param name: text`` (and ``:param type name: text``) fields.

The summary is the text before the first section, joined into one
paragraph: what the model reads as the tool's description.
"""

from __future__ import annotations

import inspect
import re
from typing import Dict, Tuple

_GOOGLE_HEAD = re.compile(r"^(Args|Arguments|Parameters|Params)\s*:\s*$")
_GOOGLE_ARG = re.compile(r"^(\*{0,2}\w+)\s*(?:\([^)]*\))?\s*:\s*(.*)$")
_SECTION = re.compile(
    r"^(Returns?|Yields?|Raises?|Examples?|Notes?|Note|See Also|Attributes|Warnings?|"
    r"References|Todo)\s*:?\s*$"
)
_NUMPY_ARG = re.compile(r"^(\*{0,2}\w+)\s*(?::\s*.*)?$")
_SPHINX_PARAM = re.compile(r"^:param\s+(?:[^:]*\s)?(\w+)\s*:\s*(.*)$")
_SPHINX_FIELD = re.compile(r"^:\w+")

__all__ = ["parse_docstring"]


def parse_docstring(doc: str | None) -> Tuple[str, Dict[str, str]]:
    """``(summary, {argument: description})``; both empty for no docstring."""
    if not doc:
        return "", {}
    lines = inspect.cleandoc(doc).splitlines()
    summary_lines = []
    for index, line in enumerate(lines):
        stripped = line.strip()
        if (
            _GOOGLE_HEAD.match(stripped)
            or _SECTION.match(stripped)
            or _SPHINX_FIELD.match(stripped)
            or _is_numpy_heading(lines, index)
        ):
            break
        summary_lines.append(stripped)
    summary = " ".join(" ".join(summary_lines).split())
    args = _google(lines) or _numpy(lines) or _sphinx(lines)
    return summary, args


def _is_numpy_heading(lines, index: int) -> bool:
    line = lines[index]
    nxt = lines[index + 1].strip() if index + 1 < len(lines) else ""
    return bool(line.strip()) and bool(nxt) and set(nxt) == {"-"}


def _join(parts) -> str:
    return " ".join(" ".join(parts).split())


def _google(lines) -> Dict[str, str]:
    out: Dict[str, list] = {}
    inside, indent, current = False, None, None
    for line in lines:
        stripped = line.strip()
        if _GOOGLE_HEAD.match(stripped):
            inside, indent, current = True, None, None
            continue
        if not inside:
            continue
        if not stripped:
            continue
        here = len(line) - len(line.lstrip())
        if here == 0:
            break  # the next section, or prose after the arguments
        if indent is None:
            indent = here
        match = _GOOGLE_ARG.match(stripped)
        if here == indent and match:
            current = match.group(1).lstrip("*")
            out[current] = [match.group(2)]
        elif current is not None:
            out[current].append(stripped)
    return {name: _join(parts) for name, parts in out.items()}


def _numpy(lines) -> Dict[str, str]:
    out: Dict[str, list] = {}
    for i, line in enumerate(lines):
        if line.strip() in ("Parameters", "Arguments") and i + 1 < len(lines):
            if set(lines[i + 1].strip()) == {"-"}:
                start = i + 2
                break
    else:
        return {}
    current = None
    for j in range(start, len(lines)):
        line = lines[j]
        stripped = line.strip()
        if j + 1 < len(lines) and stripped and set(lines[j + 1].strip()) == {"-"}:
            break  # the next heading
        if not stripped:
            continue
        if line == line.lstrip():
            match = _NUMPY_ARG.match(stripped)
            current = match.group(1).lstrip("*") if match else None
            if current:
                out[current] = []
        elif current is not None:
            out[current].append(stripped)
    return {name: _join(parts) for name, parts in out.items()}


def _sphinx(lines) -> Dict[str, str]:
    out: Dict[str, list] = {}
    current = None
    for line in lines:
        stripped = line.strip()
        match = _SPHINX_PARAM.match(stripped)
        if match:
            current = match.group(1)
            out[current] = [match.group(2)]
        elif _SPHINX_FIELD.match(stripped):
            current = None
        elif current is not None and stripped:
            out[current].append(stripped)
    return {name: _join(parts) for name, parts in out.items()}
