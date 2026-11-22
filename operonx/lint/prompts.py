"""C19: a project's prompts are objects in ``prompts/``, linked, never glued in code.

- An object that does not load is an **error**: a composite with an unknown
  key, a link to a prompt that is not there, or a ``{{> other}}`` include in a
  text object fails here, not on a call.
- ``.render(x=prompt(...))`` — a prompt given as a variable's value — is an
  **error** too: it raises when it runs; link the prompt from a composite.
- The rest are **warnings**:

  * ``<<x>>`` or a single-brace ``{x}`` in a text object: data is ``{{x}}``;
  * ``prompt(...).format(…)`` / ``.replace`` / ``.format_map`` (also on
    ``.text``): text built in code; data is ``{{x}}``, ``.render`` fills it;
  * ``LLMOp(prompt="…")`` with prose in the literal (a line break, or
    longer than :data:`INLINE_MAX`): the text belongs in ``prompts/``.

Like the other rules, this reads source only (``ast`` and the prompt files).
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Iterable, Iterator, List

from operonx.lint.rules import Finding, _allowed, _is_test_file

__all__ = ["INLINE_MAX", "lint_prompts"]

#: The longest ``prompt=`` literal that is a template, not prose.
INLINE_MAX = 160

_ANGLE = re.compile(r"<<\s*[A-Za-z_][A-Za-z0-9_]*\s*>>")
_SINGLE = re.compile(r"(?<!\{)\{([A-Za-z_][A-Za-z0-9_]*)\}(?!\})")


def _files(root: Path) -> Iterator[Path]:
    base = root / "prompts"
    if not base.is_dir():
        return
    for path in sorted(base.rglob("*")):
        rel = path.relative_to(base)
        if (
            path.is_file()
            and path.suffix in (".md", ".yaml")
            and not any(p.startswith(".") for p in rel.parts)
        ):
            yield path


def _objects(root: Path) -> Iterator[Finding]:
    from operonx import prompts as P

    base = root / "prompts"
    for path in _files(root):
        name = path.relative_to(base).with_suffix("").as_posix()
        token = P._FILES_ONLY.set(True)
        try:
            P.prompt(name, root=root)
        except (P.PromptNotFound, P.PromptFormatError, ValueError) as exc:
            yield Finding("C19", path, 1, f"prompt {name!r} does not load: {exc}")
            continue
        finally:
            P._FILES_ONLY.reset(token)
        if path.suffix != ".md":
            continue
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            m = _ANGLE.search(line)
            if m:
                var = m.group(0).strip("<> ")
                yield Finding(
                    "C19",
                    path,
                    i,
                    f"{m.group(0)} is not filled in: data is {{{{{var}}}}}",
                    "warning",
                )
            m = _SINGLE.search(line)
            if m:
                yield Finding(
                    "C19",
                    path,
                    i,
                    f"{{{m.group(1)}}} is not filled in: data is {{{{{m.group(1)}}}}}",
                    "warning",
                )


def _is_prompt_call(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call):
        return False
    f = node.func
    name = f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else ""
    return name == "prompt"


def _code(tree: ast.AST, path: Path) -> Iterator[Finding]:
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if isinstance(f, ast.Attribute) and f.attr in ("format", "format_map", "replace"):
            on = (
                f.value.value
                if isinstance(f.value, ast.Attribute) and f.value.attr == "text"
                else f.value
            )
            if _is_prompt_call(on):
                yield Finding(
                    "C19",
                    path,
                    node.lineno,
                    f".{f.attr}() builds text from a prompt: data is {{{{x}}}} and .render(x=…) fills it",
                    "warning",
                )
        if isinstance(f, ast.Attribute) and f.attr == "render":
            for kw in node.keywords:
                if kw.arg and _is_prompt_call(kw.value):
                    yield Finding(
                        "C19",
                        path,
                        node.lineno,
                        f"{kw.arg}= is given a prompt: a variable is data; link the prompt from a composite (use / pick / when)",
                    )
        called = f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else ""
        if called == "LLMOp":
            for kw in node.keywords:
                if kw.arg != "prompt":
                    continue
                literals = [kw.value] if not isinstance(kw.value, ast.Dict) else kw.value.values
                for lit in literals:
                    if (
                        isinstance(lit, ast.Constant)
                        and isinstance(lit.value, str)
                        and ("\n" in lit.value.strip() or len(lit.value) > INLINE_MAX)
                    ):
                        yield Finding(
                            "C19",
                            path,
                            lit.lineno,
                            'prompt= is prose in the code: make it a prompt object in prompts/ and pass prompt("<name>")',
                            "warning",
                        )


def lint_prompts(root: Path, files: Iterable[Path]) -> List[Finding]:
    """C19 for the project at *root*: its ``prompts/`` and its Python *files*."""
    root = Path(root)
    out: List[Finding] = list(_objects(root))
    for path in files:
        if _is_test_file(path.relative_to(root)):
            continue
        try:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source)
        except (SyntaxError, UnicodeDecodeError):
            continue
        allow = _allowed(source)
        out += [f for f in _code(tree, path) if f.rule not in allow.get(f.line, ())]
    return out
