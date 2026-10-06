"""`operonx init` — a new project laid out the way the guide says.

The templates are package data under ``operonx/cli/templates/``:
``_common/`` holds what every project gets, ``<template>/`` what one
template adds or replaces. A file there is ``<path>.tmpl``; a leading
``dot-`` in a path part becomes ``.`` (``dot-gitignore.tmpl`` →
``.gitignore``), so no dotfile has to survive packaging. ``{{name}}``,
``{{dist}}``, ``{{version}}``, ``{{extras}}``, ``{{requires}}`` and
``{{summary}}`` are filled in; nothing else is, so Python braces need
no escaping.

The project also gets ``.operonx/guide/``, the guides of every installed
operonx package, so an assistant reads them beside the code (what
``operonx guide`` writes).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

__all__ = ["TEMPLATES", "Template", "InitError", "plan", "init_project"]

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
COMMON = "_common"

_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")


@dataclass(frozen=True)
class Template:
    name: str
    summary: str
    extras: str
    #: The commands after `cd` — what the user runs next, in order.
    next_steps: tuple
    #: Packages the template's code needs besides operonx (requirement
    #: strings), added to the generated pyproject's dependencies.
    requires: tuple = ()


TEMPLATES: Dict[str, Template] = {
    t.name: t
    for t in (
        Template(
            "hello",
            "A pure-compute feature, run as a job and served over HTTP.",
            "serve",
            ("uv run pytest", "uv run operonx run greet_people", "uv run operonx serve"),
        ),
        Template(
            "http",
            "An HTTP service on a doors graph.",
            "serve",
            ("uv run pytest", "uv run operonx serve --list", "uv run operonx serve"),
        ),
        Template(
            "chat",
            "A chat service: one LLMOp on the llm:assistant model.",
            "serve,openai",
            (
                "uv run pytest",
                "cp .env.example .env   # then set LLM_API_KEY",
                "uv run operonx serve",
            ),
        ),
        Template(
            "agent",
            "An agent with one tool (operonx-agents), served over HTTP.",
            "serve,openai",
            (
                "uv run pytest",
                "cp .env.example .env   # then set LLM_API_KEY",
                "uv run operonx serve",
            ),
            # operonx cannot depend on operonx-agents; the project does
            requires=("operonx-agents>=0.1.0.dev0",),
        ),
    )
}


class InitError(Exception):
    """The project cannot be written as asked."""


@dataclass
class InitResult:
    root: Path
    name: str
    template: str
    created: List[str] = field(default_factory=list)
    overwritten: List[str] = field(default_factory=list)
    kept: List[str] = field(default_factory=list)


def _target(rel: Path) -> str:
    parts = ["." + p[len("dot-") :] if p.startswith("dot-") else p for p in rel.parts]
    parts[-1] = parts[-1][: -len(".tmpl")]
    return "/".join(parts)


def _render(text: str, values: Dict[str, str]) -> str:
    for key, value in values.items():
        text = text.replace("{{" + key + "}}", value)
    return text


def operonx_checkout(start: Path) -> Optional[Path]:
    """The operonx source checkout *start* is inside, if any: the nearest
    ``pyproject.toml`` above it that names the ``operonx`` project."""
    try:
        import tomllib
    except ImportError:  # pragma: no cover - 3.10
        import tomli as tomllib

    for folder in [start, *start.parents]:
        manifest = folder / "pyproject.toml"
        if not manifest.is_file():
            continue
        try:
            name = (
                tomllib.loads(manifest.read_text(encoding="utf-8")).get("project", {}).get("name")
            )
        except (OSError, ValueError):
            continue
        if name == "operonx":
            return folder
    return None


def _sources(root: Path, editable: Optional[Path]) -> str:
    """The pyproject's ``[tool.uv.sources]`` pinning an operonx checkout."""
    if editable is None:
        return ""
    target = Path(editable).resolve()
    if not (target / "operonx" / "__init__.py").is_file():
        raise InitError(f"--editable {editable}: no operonx package there (no operonx/__init__.py)")
    try:
        where = Path(os.path.relpath(target, root.resolve())).as_posix()
    except ValueError:  # another drive
        where = target.as_posix()
    return (
        "\n# operonx from a checkout, not PyPI (`operonx init --editable`)\n"
        f'[tool.uv.sources]\noperonx = {{ path = "{where}", editable = true }}\n'
    )


def plan(
    template: str, name: str, *, root: Optional[Path] = None, editable: Optional[Path] = None
) -> Dict[str, bytes]:
    """Every file *template* writes for a project called *name*, by path.
    With *editable*, the project's operonx is that checkout."""
    from operonx import __version__

    if template not in TEMPLATES:
        raise InitError(f"no template {template!r} (have: {', '.join(TEMPLATES)})")
    if not _NAME.match(name):
        raise InitError(
            f"project name {name!r} must start with a letter and hold only letters, "
            "digits, '-' and '_' — pass --name"
        )
    t = TEMPLATES[template]
    values = {
        "name": name,
        "dist": name.replace("_", "-").lower(),
        "version": __version__,
        "extras": t.extras,
        "summary": t.summary,
        "requires": "".join(f'\n    "{r}",' for r in t.requires),
        "sources": _sources(Path(root or "."), editable),
    }
    files: Dict[str, bytes] = {}
    for layer in (COMMON, template):
        base = TEMPLATE_DIR / layer
        for src in sorted(base.rglob("*.tmpl")):
            text = src.read_text(encoding="utf-8")
            files[_target(src.relative_to(base))] = _render(text, values).encode("utf-8")
    return files


def init_project(
    root: Path,
    *,
    template: str = "hello",
    name: str = None,
    force: bool = False,
    editable: Optional[Path] = None,
) -> InitResult:
    """Write *template* into *root*. An existing file is kept unless *force*.

    *editable* pins operonx to that checkout (``[tool.uv.sources]``); a
    project made inside an operonx checkout pins it without being asked —
    there, PyPI's operonx is never the one being worked on."""
    root = Path(root)
    if root.exists() and not root.is_dir():
        raise InitError(f"{root} exists and is not a directory")
    name = name or root.resolve().name
    if editable is None:
        editable = operonx_checkout(root.resolve())
    files = plan(template, name, root=root, editable=editable)
    result = InitResult(root=root, name=name, template=template)
    for rel, content in files.items():
        path = root / rel
        if path.exists():
            if not force:
                result.kept.append(rel)
                continue
            result.overwritten.append(rel)
        else:
            result.created.append(rel)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    from operonx import guide

    guide.sync(root)  # generated, so always current — never "kept"
    return result
