"""`operonx init` — a new project laid out the way the guide says.

The templates are package data under ``operonx/cli/templates/``:
``_common/`` holds what every project gets, ``<template>/`` what one
template adds or replaces. A file there is ``<path>.tmpl``; a leading
``dot-`` in a path part becomes ``.`` (``dot-gitignore.tmpl`` →
``.gitignore``), so no dotfile has to survive packaging. ``{{name}}``,
``{{dist}}``, ``{{version}}``, ``{{extras}}``, ``{{requires}}`` and
``{{summary}}`` are filled in, and the toolchain's commands (``{{run}}``,
``{{setup}}``, ``{{upgrade}}``, ``{{add}}``, ``{{shell}}``, see
:data:`TOOLCHAINS`); nothing else is, so Python braces need no escaping.

A project is set up and run with uv or with plain pip (``--uv`` /
``--pip``; by default uv when it is on PATH). Only the commands the
project's docs name differ: ``pyproject.toml`` works with both, its test
tools being the ``test`` extra that uv's ``dev`` group points at.

The project also gets ``.operonx/guide/``, the guides of every installed
operonx package, so an assistant reads them beside the code (what
``operonx guide`` writes), and, unless ``--no-brd``, ``docs/brd/``: the
living BRD (requirements, their checklist, open questions) that
``operonx-project`` builds from and the studio's Board shows. Its template
is ``templates/_brd/`` (:func:`brd_files`), the one ``operonx-project brd
init`` writes too.
"""

from __future__ import annotations

import datetime as _dt
import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

__all__ = [
    "TEMPLATES",
    "TOOLCHAINS",
    "BRD_DIR",
    "Template",
    "brd_files",
    "InitError",
    "default_toolchain",
    "plan",
    "init_project",
]

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
COMMON = "_common"
BRD = "_brd"
#: How a new project is built (operonx-project's practices.toml and LESSONS.md): written by ``operonx init``
#: only, never by ``operonx-project brd init`` on an existing repo, where no file means the builder's defaults.
PRACTICES = "_practices"
#: Where a project's BRD folder goes.
BRD_DIR = "docs/brd"

_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")


@dataclass(frozen=True)
class Template:
    name: str
    summary: str
    extras: str
    #: The commands after setting up — what the user runs next, in order.
    #: ``{run}`` is the toolchain's prefix (``uv run `` or nothing).
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
            ("{run}pytest", "{run}operonx run greet_people", "{run}operonx serve"),
        ),
        Template(
            "http",
            "An HTTP service on a doors graph.",
            "serve",
            ("{run}pytest", "{run}operonx serve --list", "{run}operonx serve"),
        ),
        Template(
            "chat",
            "A chat service: one LLMOp on the llm:assistant model.",
            "serve,openai",
            (
                "{run}pytest",
                "cp .env.example .env   # then set LLM_API_KEY",
                "{run}operonx serve",
            ),
        ),
        Template(
            "agent",
            "An agent with one tool (operonx-agents), served over HTTP.",
            "serve,openai",
            (
                "{run}pytest",
                "cp .env.example .env   # then set LLM_API_KEY",
                "{run}operonx serve",
            ),
            # operonx cannot depend on operonx-agents; the project does
            requires=("operonx-agents>=0.1.4",),
        ),
    )
}


#: The commands a project's docs name, per toolchain. With pip, the
#: project's ``.venv`` is made and activated by hand, so a command needs
#: no prefix once it is active.
TOOLCHAINS: Dict[str, Dict[str, str]] = {
    "uv": {
        "run": "uv run ",
        "setup": "uv sync",
        "upgrade": "uv lock --upgrade-package operonx && uv sync",
        "add": "uv add",
        "shell": "",
    },
    "pip": {
        "run": "",
        "setup": (
            "python -m venv .venv\n"
            "source .venv/bin/activate   # Windows: .venv\\Scripts\\activate\n"
            'pip install -e ".[test]"'
        ),
        "upgrade": "pip install -U operonx",
        "add": "pip install",
        "shell": (
            "Run every command in the project's `.venv`: `source .venv/bin/activate`\n"
            "first in each new shell (Windows: `.venv\\Scripts\\activate`).\n\n"
        ),
    },
}


def default_toolchain() -> str:
    """uv when it is installed, else pip."""
    return "uv" if shutil.which("uv") else "pip"


def next_steps(template: str, toolchain: str) -> List[str]:
    """What to run after ``cd``: set up, then the template's commands."""
    t = TOOLCHAINS[toolchain]
    return [
        *t["setup"].split("\n"),
        *(s.format(run=t["run"]) for s in TEMPLATES[template].next_steps),
    ]


class InitError(Exception):
    """The project cannot be written as asked."""


@dataclass
class InitResult:
    root: Path
    name: str
    template: str
    toolchain: str = "uv"
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


_COMMENT = re.compile(r"^(\S.*?\S|\S)\s{2,}(# .*)$")


def _align(markdown: str) -> str:
    """Line up the ``# comments`` of each shell block: a toolchain's commands
    differ in length, so no column can be written into the template."""
    out: List[str] = []
    block: Optional[List[str]] = None
    for line in markdown.split("\n"):
        if block is None:
            out.append(line)
            if line.startswith("```bash"):
                block = []
        elif line.startswith("```"):
            found = [_COMMENT.match(b) for b in block]
            # a long one-off line (a curl) keeps its comment beside it
            width = max((len(m.group(1)) for m in found if m and len(m.group(1)) <= 40), default=0)
            out += [
                f"{m.group(1):<{max(width, len(m.group(1))) + 3}}{m.group(2)}" if m else b
                for b, m in zip(block, found)
            ]
            out.append(line)
            block = None
        else:
            block.append(line)
    return "\n".join(out + (block or []))


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


def brd_files(name: str, date: Optional[str] = None) -> Dict[str, bytes]:
    """The BRD folder's files for a project called *name*, by path inside the
    folder (``README.md``, ``requirements/requirements_traceability.md``, …)."""
    values = {"name": name, "date": date or _dt.date.today().isoformat()}
    base = TEMPLATE_DIR / BRD
    return {
        _target(src.relative_to(base)): _render(src.read_text(encoding="utf-8"), values).encode(
            "utf-8"
        )
        for src in sorted(base.rglob("*.tmpl"))
    }


def plan(
    template: str,
    name: str,
    *,
    root: Optional[Path] = None,
    editable: Optional[Path] = None,
    toolchain: str = "uv",
    brd: bool = True,
) -> Dict[str, bytes]:
    """Every file *template* writes for a project called *name*, by path,
    its docs naming *toolchain*'s commands. With *editable*, the project's
    operonx is that checkout; with *brd*, it has a BRD folder (``docs/brd``)."""
    from operonx import __version__
    from operonx.guide import builder_block

    if template not in TEMPLATES:
        raise InitError(f"no template {template!r} (have: {', '.join(TEMPLATES)})")
    if not _NAME.match(name):
        raise InitError(
            f"project name {name!r} must start with a letter and hold only letters, "
            "digits, '-' and '_' — pass --name"
        )
    if toolchain not in TOOLCHAINS:
        raise InitError(f"no toolchain {toolchain!r} (have: {', '.join(TOOLCHAINS)})")
    t = TEMPLATES[template]
    values = {
        **TOOLCHAINS[toolchain],
        "name": name,
        "dist": name.replace("_", "-").lower(),
        "version": __version__,
        "extras": t.extras,
        "summary": t.summary,
        "requires": "".join(f'\n    "{r}",' for r in t.requires),
        "sources": _sources(Path(root or "."), editable),
        "brd": "\n" + builder_block() + "\n" if brd else "",
    }
    files: Dict[str, bytes] = {}
    for layer in (COMMON, template):
        base = TEMPLATE_DIR / layer
        for src in sorted(base.rglob("*.tmpl")):
            target = _target(src.relative_to(base))
            text = _render(src.read_text(encoding="utf-8"), values)
            if target.endswith(".md"):
                text = _align(text)
            files[target] = text.encode("utf-8")
    if brd:
        files.update({f"{BRD_DIR}/{rel}": text for rel, text in brd_files(name).items()})
        base = TEMPLATE_DIR / PRACTICES
        files.update({f"{BRD_DIR}/{_target(src.relative_to(base))}": src.read_bytes()
                      for src in sorted(base.rglob("*.tmpl"))})  # fmt: skip
    return files


def init_project(
    root: Path,
    *,
    template: str = "hello",
    name: str = None,
    force: bool = False,
    editable: Optional[Path] = None,
    toolchain: Optional[str] = None,
    brd: bool = True,
) -> InitResult:
    """Write *template* into *root*. An existing file is kept unless *force*.
    Its docs name *toolchain*'s commands (``"uv"`` or ``"pip"``; by default
    uv when it is installed).

    *editable* pins operonx to that checkout (``[tool.uv.sources]``); a
    project made inside an operonx checkout pins it without being asked —
    there, PyPI's operonx is never the one being worked on. Without *brd*,
    no ``docs/brd/``."""
    root = Path(root)
    if root.exists() and not root.is_dir():
        raise InitError(f"{root} exists and is not a directory")
    name = name or root.resolve().name
    if editable is None:
        editable = operonx_checkout(root.resolve())
    toolchain = toolchain or default_toolchain()
    files = plan(template, name, root=root, editable=editable, toolchain=toolchain, brd=brd)
    result = InitResult(root=root, name=name, template=template, toolchain=toolchain)
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

    guide.sync(root, toolchain=toolchain)  # generated, so always current — never "kept"
    return result
