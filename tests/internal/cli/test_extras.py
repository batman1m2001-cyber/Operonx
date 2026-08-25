"""Every install hint must name an extra that actually exists.

Regression guard, companion to ``test_entry_points.py``. Commit 1f830c7
deleted the ``providers`` extra while leaving nine in-code references and
the install-tier comment pointing at it, so
``pip install operonx[providers]`` — the fix our own ImportError told users
to run — resolved to nothing. pip does not fail on an unknown extra; it
warns and installs the base package, so the advice appeared to work and
then the same ImportError came back.

``DocStoreType.MONGO``/``REDIS`` had the same shape from the other
direction: an install hint for backends that have no module at all.

These are only reachable when a user hits a missing optional dependency, so
nothing exercises them in CI unless something like this does.
"""

from __future__ import annotations

import ast
import io
import re
import tokenize
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

try:  # tomllib is stdlib from 3.11
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - exercised on 3.10 only
    import tomli as tomllib  # type: ignore[no-redef]

ROOT = Path(__file__).resolve().parents[3]
PACKAGE = ROOT / "operonx"

_LITERAL = re.compile(r"pip install operonx\[([a-z0-9_,\- ]+)\]")


def declared_extras() -> set[str]:
    with (ROOT / "pyproject.toml").open("rb") as fh:
        return set(tomllib.load(fh).get("project", {}).get("optional-dependencies", {}))


def _python_files():
    return sorted(p for p in PACKAGE.rglob("*.py") if "__pycache__" not in p.parts)


def literal_hints() -> list[tuple[Path, int, str]]:
    """Hardcoded ``pip install operonx[x]`` strings, comments excluded.

    A comment may legitimately quote a *wrong* command while explaining a
    past bug; only what reaches a user matters here.
    """
    out = []
    for path in _python_files():
        source = path.read_text(encoding="utf-8")
        try:
            tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
        except (tokenize.TokenError, IndentationError):  # pragma: no cover
            continue
        for tok in tokens:
            if tok.type != tokenize.STRING:
                continue
            for match in _LITERAL.finditer(tok.string):
                for name in match.group(1).split(","):
                    name = name.strip()
                    if name and not name.startswith("{"):
                        out.append((path, tok.start[0], name))
    return out


def message_helper_hints() -> list[tuple[Path, int, str]]:
    """``_missing_extra_message("Backend", "extra", exc)`` call sites."""
    out = []
    for path in _python_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            name = fn.id if isinstance(fn, ast.Name) else getattr(fn, "attr", "")
            if name != "_missing_extra_message" or len(node.args) < 2:
                continue
            extra = node.args[1]
            if isinstance(extra, ast.Constant) and isinstance(extra.value, str):
                out.append((path, node.lineno, extra.value))
    return out


def lazy_backend_hints() -> list[tuple[Path, int, str]]:
    """``_LAZY_BACKENDS = {"Name": ("module", "extra")}`` tables."""
    out = []
    for path in _python_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover
            continue
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Assign) and isinstance(node.value, ast.Dict)):
                continue
            targets = {t.id for t in node.targets if isinstance(t, ast.Name)}
            if not targets & {"_LAZY_BACKENDS", "_LAZY"}:
                continue
            for value in node.value.values:
                if isinstance(value, ast.Tuple) and len(value.elts) == 2:
                    extra = value.elts[1]
                    if isinstance(extra, ast.Constant) and isinstance(extra.value, str):
                        out.append((path, value.lineno, extra.value))
    return out


ALL_HINTS = literal_hints() + message_helper_hints() + lazy_backend_hints()


def test_hints_are_actually_present():
    """Guard the guard — if the scan finds nothing, it has stopped working."""
    assert len(ALL_HINTS) >= 10, "install-hint scan found suspiciously few sites"


@pytest.mark.parametrize(
    "path, lineno, extra",
    [pytest.param(p, n, e, id=f"{p.relative_to(ROOT)}:{n}:{e}") for p, n, e in ALL_HINTS],
)
def test_every_install_hint_names_a_real_extra(path, lineno, extra):
    available = declared_extras()
    assert extra in available, (
        f"{path.relative_to(ROOT)}:{lineno} tells users to run "
        f"'pip install operonx[{extra}]', but no such extra is declared. "
        f"Available: {', '.join(sorted(available))}"
    )
