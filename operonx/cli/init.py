"""`operonx init` — a new project laid out the way the guide says.

The templates are package data under ``operonx/cli/templates/``:
``_common/`` holds what every project gets, ``<template>/`` what one
template adds or replaces. A file there is ``<path>.tmpl``; a leading
``dot-`` in a path part becomes ``.`` (``dot-gitignore.tmpl`` →
``.gitignore``), so no dotfile has to survive packaging. ``{{name}}``,
``{{dist}}``, ``{{version}}``, ``{{extras}}`` and ``{{summary}}`` are
filled in; nothing else is, so Python braces need no escaping.

The project also gets ``.operonx/guide/``, the installed guide, so an
assistant reads it beside the code (what ``operonx guide --sync`` writes).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List

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
            "A ReAct agent with one tool, served over HTTP.",
            "serve,openai",
            (
                "uv run pytest",
                "cp .env.example .env   # then set LLM_API_KEY",
                "uv run operonx serve",
            ),
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


def plan(template: str, name: str) -> Dict[str, bytes]:
    """Every file *template* writes for a project called *name*, by path."""
    from operonx import __version__, guide

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
    }
    files: Dict[str, bytes] = {}
    for layer in (COMMON, template):
        base = TEMPLATE_DIR / layer
        for src in sorted(base.rglob("*.tmpl")):
            text = src.read_text(encoding="utf-8")
            files[_target(src.relative_to(base))] = _render(text, values).encode("utf-8")
    copy = guide.COPY_DIR.as_posix()
    for page in guide.pages():
        files[f"{copy}/{page.name}"] = page.read_bytes()
    files[f"{copy}/VERSION"] = f"{__version__}\n".encode()
    return files


def init_project(
    root: Path, *, template: str = "hello", name: str = None, force: bool = False
) -> InitResult:
    """Write *template* into *root*. An existing file is kept unless *force*."""
    root = Path(root)
    if root.exists() and not root.is_dir():
        raise InitError(f"{root} exists and is not a directory")
    name = name or root.resolve().name
    files = plan(template, name)
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
    return result
