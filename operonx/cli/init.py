"""`operonx init` — a new project laid out the way the guide says.

The templates are package data under ``operonx/cli/templates/``:
``_common/`` holds what every project gets, ``<template>/`` what one
template adds or replaces. A file there is ``<path>.tmpl``; a leading
``dot-`` in a path part becomes ``.`` (``dot-gitignore.tmpl`` →
``.gitignore``), so no dotfile has to survive packaging. ``{{key}}`` is
filled from :func:`plan`'s values; nothing else is, so Python braces need
no escaping. ``{{#x}}…{{/x}}`` is kept when *x* is on (the template's name,
a part in :data:`PARTS`, ``llm``), ``{{^x}}…{{/x}}`` when it is off; a tag
alone on its line takes the line with it. A file that is one section and
renders empty is not written: ``routes.py`` exists only with ``--http``.

The parts a project has are ``parts`` in its ``operonx.toml``.
``operonx init . --web`` in a project adds the part: it writes the files
that are missing, rewrites a file still as init wrote it, and for a file
the owner edited reports the lines to add by hand.

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
from typing import Dict, List, Optional, Sequence, Set

__all__ = [
    "PARTS",
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
#: Where a project's BRD folder goes.
BRD_DIR = "docs/brd"

_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")


#: What a flag adds; any of them, in this order. ``web`` turns on ``http``.
PARTS = ("http", "web", "deploy")


@dataclass(frozen=True)
class Template:
    name: str
    summary: str
    #: The one feature's folder under ``src/``.
    feature: str
    #: Its job, and what the job does (README's comment).
    job: str
    job_says: str
    #: The route ``--http`` serves: its path, the body field the page sends,
    #: the reply field it shows, what else the body carries (a JS object)
    #: and a curl body.
    route: tuple
    #: Packages the template's code needs besides operonx (requirement
    #: strings), added to the generated pyproject's dependencies.
    requires: tuple = ()
    #: It calls a model: ``llm:assistant`` in resources.yaml, LLM_API_KEY.
    llm: bool = False


TEMPLATES: Dict[str, Template] = {
    t.name: t
    for t in (
        Template(
            "hello",
            "Pure compute: ops and a graph, run as a job. Offline, no key.",
            "greeting",
            "greet_people",
            "data/people.jsonl → out/greetings.jsonl",
            ("/greet", "full_name", "greeting", '{ id: "web" }', '{"id": "1", "full_name": "ada"}'),
        ),
        Template(
            "chat",
            "One model call (LLMOp) on llm:assistant, with its prompt.",
            "chat",
            "answer_questions",
            "data/questions.jsonl → out/answers.jsonl",
            ("/chat", "question", "answer", "{}", '{"question": "What is a graph?"}'),
            llm=True,
        ),
        Template(
            "agent",
            "An agent with one tool (operonx-agents).",
            "assistant",
            "ask_assistant",
            "data/questions.jsonl → out/answers.jsonl",
            ("/ask", "input", "output", "{}", '{"input": "How many words in: one two three?"}'),
            # operonx cannot depend on operonx-agents; the project does
            requires=("operonx-agents>=0.1.4",),
            llm=True,
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


def next_steps(template: str, toolchain: str, parts: Sequence[str] = ()) -> List[str]:
    """What to run after ``cd``: set up, test, then run what the parts add."""
    t, run = TEMPLATES[template], TOOLCHAINS[toolchain]["run"]
    return [
        *TOOLCHAINS[toolchain]["setup"].split("\n"),
        f"{run}pytest",
        *(["cp .env.example .env   # then set LLM_API_KEY"] if t.llm else []),
        f"{run}operonx run {t.job}",
        *([f"{run}operonx serve"] if "http" in parts else []),
        *(["docker compose -f deploy/compose.yml up --build"] if "deploy" in parts else []),
    ]


def parts_of(flags: Sequence[str]) -> List[str]:
    """*flags* in :data:`PARTS` order, ``web`` bringing ``http``."""
    on = set(flags) | ({"http"} if "web" in flags else set())
    unknown = on - set(PARTS)
    if unknown:
        raise InitError(f"no part {', '.join(sorted(unknown))} (have: {', '.join(PARTS)})")
    return [p for p in PARTS if p in on]


class InitError(Exception):
    """The project cannot be written as asked."""


@dataclass
class InitResult:
    root: Path
    name: str
    template: str
    toolchain: str = "uv"
    parts: List[str] = field(default_factory=list)
    created: List[str] = field(default_factory=list)
    overwritten: List[str] = field(default_factory=list)
    kept: List[str] = field(default_factory=list)
    #: Files still as init wrote them, rewritten for the new parts.
    updated: List[str] = field(default_factory=list)
    #: Files the owner edited that the new parts change: path → the lines
    #: to add by hand.
    edited: Dict[str, List[str]] = field(default_factory=dict)


def _target(rel: Path) -> str:
    parts = ["." + p[len("dot-") :] if p.startswith("dot-") else p for p in rel.parts]
    parts[-1] = parts[-1][: -len(".tmpl")]
    return "/".join(parts)


def _render(text: str, values: Dict[str, str]) -> str:
    for key, value in values.items():
        text = text.replace("{{" + key + "}}", value)
    return text


_STANDALONE = re.compile(r"^[ \t]*(\{\{[#^/]\w+\}\})[ \t]*\n", re.M)
_SECTION = re.compile(r"\{\{([#^])(\w+)\}\}(.*?)\{\{/\2\}\}", re.S)


def _sections(text: str, on: Set[str]) -> str:
    """Keep ``{{#x}}`` sections whose *x* is on and ``{{^x}}`` ones whose
    *x* is off; drop the rest. Different names nest."""
    text = _STANDALONE.sub(r"\1", text)
    while True:
        new = _SECTION.sub(lambda m: m[3] if (m[2] in on) == (m[1] == "#") else "", text)
        if new == text:
            return text
        text = new


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
    parts: Sequence[str] = (),
    root: Optional[Path] = None,
    editable: Optional[Path] = None,
    toolchain: str = "uv",
    brd: bool = True,
) -> Dict[str, bytes]:
    """Every file *template* with *parts* writes for a project called
    *name*, by path, its docs naming *toolchain*'s commands. With
    *editable*, the project's operonx is that checkout; with *brd*, it has a
    BRD folder (``docs/brd``)."""
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
    parts = parts_of(parts)
    on = {template, *parts, *(["llm"] if t.llm else [])}
    extras = [x for x, yes in (("serve", "http" in on), ("openai", t.llm)) if yes]
    path, field_, answer, body, example = t.route
    values = {
        **TOOLCHAINS[toolchain],
        "name": name,
        "dist": name.replace("_", "-").lower(),
        "version": __version__,
        "operonx": f"operonx[{','.join(extras)}]" if extras else "operonx",
        "summary": t.summary,
        "feature": t.feature,
        "job": t.job,
        "job_says": t.job_says,
        "route_path": path,
        "route_field": field_,
        "route_answer": answer,
        "route_body": body,
        "route_example": example,
        "parts": "[" + ", ".join(f'"{x}"' for x in [template, *parts]) + "]",
        "requires": "".join(f'\n    "{r}",' for r in t.requires),
        "sources": _sources(Path(root or "."), editable),
        "brd": "\n" + builder_block() + "\n" if brd else "",
    }
    files: Dict[str, bytes] = {}
    for layer in (COMMON, template):
        base = TEMPLATE_DIR / layer
        for src in sorted(base.rglob("*.tmpl")):
            target = _target(src.relative_to(base))
            raw = src.read_text(encoding="utf-8")
            text = _render(_sections(raw, on), values)
            if raw.startswith(("{{#", "{{^")) and not text.strip():
                continue  # a part that is off
            if target.endswith(".md"):
                text = _align(text)
            files[target] = text.encode("utf-8")
    if brd:
        files.update({f"{BRD_DIR}/{rel}": text for rel, text in brd_files(name).items()})
    return files


def _manifest(root: Path) -> dict:
    """The ``[project]`` table of *root*'s operonx.toml, or ``{}``."""
    try:
        import tomllib
    except ImportError:  # pragma: no cover - 3.10
        import tomli as tomllib

    try:
        return tomllib.loads((root / "operonx.toml").read_text(encoding="utf-8")).get("project", {})
    except (OSError, ValueError):
        return {}


def _as_written(text: str) -> str:
    """*text* without the blocks ``operonx guide`` rewrites."""
    from operonx import guide as g

    for begin, end in (
        (g.AGENTS_BEGIN, g.AGENTS_END),
        (g.COMMANDS_BEGIN, g.COMMANDS_END),
        (g.BUILDER_BEGIN, g.BUILDER_END),
    ):
        text = re.sub(re.escape(begin) + ".*?" + re.escape(end), "", text, flags=re.S)
    return text


def _missing(new: str, old: str) -> List[str]:
    """The lines of *new* that *old* has not."""
    have = {line.strip() for line in old.splitlines()}
    return [line for line in new.splitlines() if line.strip() and line.strip() not in have]


def init_project(
    root: Path,
    *,
    template: Optional[str] = None,
    parts: Sequence[str] = (),
    name: str = None,
    force: bool = False,
    editable: Optional[Path] = None,
    toolchain: Optional[str] = None,
    brd: Optional[bool] = None,
) -> InitResult:
    """Write *template* (``hello`` by default) with *parts* into *root*.
    Its docs name *toolchain*'s commands (``"uv"`` or ``"pip"``; by default
    uv when it is installed).

    In a project init made (``parts`` in its operonx.toml), the template is
    the project's and *parts* are added to its own: a missing file is
    written, a file still as init wrote it is rewritten, and an edited one
    is kept, with the lines it lacks in :attr:`InitResult.edited`. Any other
    existing file is kept unless *force*.

    *editable* pins operonx to that checkout (``[tool.uv.sources]``); a
    project made inside an operonx checkout pins it without being asked —
    there, PyPI's operonx is never the one being worked on. *brd* (by
    default: yes, or what the project has) writes ``docs/brd/``."""
    root = Path(root)
    if root.exists() and not root.is_dir():
        raise InitError(f"{root} exists and is not a directory")
    project = _manifest(root)
    had, old = project.get("parts"), []
    if had:
        if template and template != had[0]:
            raise InitError(
                f"{root} is a {had[0]} project; a template is chosen once (parts = {had})"
            )
        template, old = had[0], parts_of(had[1:])
        name = name or project.get("name")
        if brd is None:
            brd = (root / BRD_DIR).is_dir()
        if toolchain is None and (root / "AGENTS.md").is_file():
            agents = (root / "AGENTS.md").read_text(encoding="utf-8")
            toolchain = "uv" if "uv run " in agents else "pip"
    template = template or "hello"
    name = name or root.resolve().name
    if editable is None:
        editable = operonx_checkout(root.resolve())
    toolchain = toolchain or default_toolchain()
    brd = True if brd is None else brd
    parts = parts_of([*old, *parts])
    kw = dict(root=root, editable=editable, toolchain=toolchain, brd=brd)
    files = plan(template, name, parts=parts, **kw)
    before = plan(template, name, parts=old, **kw) if had else {}
    result = InitResult(root=root, name=name, template=template, toolchain=toolchain, parts=parts)
    for rel, content in files.items():
        path = root / rel
        if path.exists():
            disk = path.read_bytes()
            if force:
                result.overwritten.append(rel)
            elif disk == content or before.get(rel, content) == content:
                result.kept.append(rel)
                continue
            elif rel == "operonx.toml":
                [line] = [x for x in content.decode().splitlines() if x.startswith("parts")]
                text = re.sub(r"^parts\s*=.*$", line, disk.decode(), count=1, flags=re.M)
                content = text.encode()
                result.updated.append(rel)
            elif _as_written(disk.decode()) == _as_written(before[rel].decode()):
                result.updated.append(rel)
            else:
                result.edited[rel] = _missing(content.decode(), disk.decode())
                continue
        else:
            result.created.append(rel)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    from operonx import guide

    guide.sync(root, toolchain=toolchain)  # generated, so always current — never "kept"
    return result
