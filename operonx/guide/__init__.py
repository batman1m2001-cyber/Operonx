"""The guides for coding assistants — Markdown shipped with each operonx package.

operonx ships this directory; every other operonx package (operonx-agents,
operonx-kb) ships its own and registers it under the ``operonx.guides``
entry point::

    [project.entry-points."operonx.guides"]
    agents = "operonx_agents.guide"

A project keeps a copy of every installed guide in ``.operonx/guide/``, one
folder per package, plus a generated ``README.md`` index::

    .operonx/guide/README.md     each installed package, its version, its pages
    .operonx/guide/core/…
    .operonx/guide/agents/…

:func:`sync` writes it — ``operonx init``, ``operonx guide``, and any
``operonx`` command run inside a project whose copy is stale. The copy is
generated: a package that is no longer installed loses its folder. It also
keeps two marked blocks in the project's ``AGENTS.md`` current — the
guides, and the commands under "## Commands" (``operonx init``'s, with the
project's toolchain) — and the rest of that file is the user's. A project
made before the commands were marked gets the marked block when its
"## Commands" section holds nothing the block does not (an older copy of
it); a section someone edited is left alone.
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Union

__all__ = [
    "AGENTS_BEGIN",
    "AGENTS_END",
    "BUILDER_BEGIN",
    "BUILDER_END",
    "COMMANDS_BEGIN",
    "COMMANDS_END",
    "COPY_DIR",
    "Guide",
    "agents_block",
    "commands_block",
    "toolchain_of",
    "changes",
    "files",
    "find_project",
    "installed",
    "pages",
    "path",
    "sync",
]

#: Where a project keeps its copy of the guides, relative to its root.
COPY_DIR = Path(".operonx") / "guide"
#: The entry-point group a package registers its guide under.
GROUP = "operonx.guides"
#: The marked block of AGENTS.md that :func:`sync` rewrites.
AGENTS_BEGIN = "<!-- operonx:guide -->"
AGENTS_END = "<!-- /operonx:guide -->"
#: The marked block under AGENTS.md's "## Commands" that :func:`sync` rewrites.
COMMANDS_BEGIN = "<!-- operonx:commands -->"
COMMANDS_END = "<!-- /operonx:commands -->"
_COMMANDS = re.compile(r"^## Commands\n(.*?)(?=^## |\Z)", re.M | re.S)
#: The marked block on building with the BRD and the board; :func:`sync`
#: rewrites it, and adds it to a project that has ``docs/brd/`` but not it.
BUILDER_BEGIN = "<!-- operonx:builder -->"
BUILDER_END = "<!-- /operonx:builder -->"
_BUILDER = """## Building it: docs/brd and the board

- `docs/brd/` is the living BRD: what the product must do, and how far it
  is. `docs/brd/requirements/requirements_traceability.md` is the
  checklist, the only place a requirement's status is stated (its code, the
  test that shows it, by name, and the evidence). Questions are `Q-nn` in
  `docs/brd/requirements/open_questions.md`, never deleted.
- `docs/brd/board/` is how it is built: one file per milestone, every card
  with its plan (what it does, its rows, criteria, dependencies, files) and
  its status. The engine (`operonx-project`) writes it. Never edit it by
  hand: `operonx-project replan` changes a plan.
- When a change meets or alters a requirement, update its row (status,
  code, test) and add one line to `docs/brd/CHANGELOG.md`. The rules are in
  `docs/brd/MAINTAINING.md`. The checklist's summary table is counted from
  the rows by the engine: leave it.
- Work with more than one step gets a plan doc under `docs/` that starts
  with a status block: `title`, `status` (plan, in_progress, done or
  parked), `phases` (each `{id, title, status}`: open, in_progress, done or
  skipped) and the `rows` it serves. Change a phase's status in the commit
  that finishes it. `operonx-project plans` lists them; the studio's
  Project mode shows them.
- To work a card yourself: `operonx-project take <milestone> <card>` prints
  its worktree, criteria and rows; make the criteria pass there, then
  `operonx-project submit <milestone> <card>`. The review and the held-out
  tests decide, as for the engine's own cards.
- New work: rows in the checklist, then `operonx-project start --title ...
  --rows <ID> ...` (or the studio's Board) and `operonx-project daemon`.
  `.operonx/project/`, outside git, is only the running engine's state."""

_HEADER = re.compile(r"<!--\s*operonx-guide\s*(.*?)\s*-->")


@dataclass(frozen=True)
class Guide:
    """One installed package's guide."""

    name: str  # the folder in the copy: "core", "agents", "kb"
    dist: str  # the distribution: "operonx", "operonx-agents"
    version: str
    dir: Path

    def pages(self) -> List[Path]:
        """Its pages in reading order: ``README.md`` first, then the numbered ones."""
        first = [self.dir / "README.md"] if (self.dir / "README.md").is_file() else []
        return first + sorted(self.dir.glob("[0-9]*.md"))


def path() -> Path:
    """The core guide's directory."""
    return Path(__file__).resolve().parent


def pages() -> List[Path]:
    """The core guide's pages, in reading order (``README.md`` first)."""
    return _core().pages()


def _core() -> Guide:
    from operonx import __version__

    return Guide("core", "operonx", __version__, path())


def installed() -> List[Guide]:
    """Every installed package's guide: core first, then by name."""
    from importlib.metadata import entry_points

    found: Dict[str, Guide] = {}
    for ep in entry_points(group=GROUP):
        if ep.name == "core" or ep.name in found:
            continue
        try:
            module = ep.load()
        except Exception:  # a broken install must not break every command
            continue
        where = Path(getattr(module, "__file__", "") or "").resolve().parent
        if not where.is_dir():
            continue
        dist = ep.dist.name if ep.dist else ep.name
        version = ep.dist.version if ep.dist else "?"
        found[ep.name] = Guide(ep.name, dist, version, where)
    return [_core()] + [found[k] for k in sorted(found)]


def _title(page: Path) -> str:
    for line in page.read_text(encoding="utf-8").splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return page.stem


def _index(guides: List[Guide]) -> str:
    stamp = " ".join(f"{g.name}={g.version}" for g in guides)
    lines = [
        f"<!-- operonx-guide {stamp} -->",
        "# operonx guides",
        "",
        "Generated by `operonx guide` from the installed packages; do not edit.",
        "Every example in these pages is tested against the version named here.",
        "",
        "Start at [core/README.md](core/README.md), then read the page for what you use.",
    ]
    for g in guides:
        lines += ["", f"## {g.name} — {g.dist} {g.version}", ""]
        for page in g.pages():
            rel = f"{g.name}/{page.name}"
            lines.append(f"- [{rel}]({rel}) — {_title(page)}")
    lines += [
        "",
        "Another operonx package (`uv add operonx-agents` or `pip install operonx-agents`;",
        "`operonx-kb` the same) adds its guide here at the next `operonx` command, or",
        "`operonx guide`.",
        "",
    ]
    return "\n".join(lines)


def files(guides: Optional[List[Guide]] = None) -> Dict[str, bytes]:
    """The whole copy, by path relative to ``.operonx/guide/``."""
    guides = installed() if guides is None else guides
    out = {"README.md": _index(guides).encode("utf-8")}
    for g in guides:
        for page in g.pages():
            out[f"{g.name}/{page.name}"] = page.read_bytes()
    return out


def _stamp(text: str) -> Dict[str, str]:
    m = _HEADER.search(text)
    if not m:
        return {}
    return dict(kv.split("=", 1) for kv in m.group(1).split() if "=" in kv)


def _copied(project: Path) -> Dict[str, str]:
    index = project / COPY_DIR / "README.md"
    try:
        return _stamp(index.read_text(encoding="utf-8"))
    except OSError:
        return {}


def changes(project: Union[str, Path], guides: Optional[List[Guide]] = None) -> List[str]:
    """What a sync would change, one entry per package (``+agents 0.1.1``,
    ``core 1.16.0 → 1.17.0``, ``-kb``); empty when the copy is current."""
    guides = installed() if guides is None else guides
    project = Path(project)
    old = _copied(project)
    out = []
    for g in guides:
        if g.name not in old:
            out.append(f"+{g.name} {g.version}")
        elif old[g.name] != g.version:
            out.append(f"{g.name} {old[g.name]} → {g.version}")
    out += [f"-{name}" for name in old if name not in {g.name for g in guides}]
    if not out and not _matches(project / COPY_DIR, files(guides)):
        out.append("pages changed")
    return out


def _matches(dest: Path, want: Dict[str, bytes]) -> bool:
    if not dest.is_dir():
        return False
    have = {p.relative_to(dest).as_posix() for p in dest.rglob("*") if p.is_file()}
    if have != set(want):
        return False
    return all((dest / rel).read_bytes() == data for rel, data in want.items())


def toolchain_of(project: Union[str, Path]) -> str:
    """How *project* is set up: ``"uv"`` once it has a ``uv.lock`` (``uv run``
    and ``uv sync`` write one), else ``"pip"``."""
    return "uv" if (Path(project) / "uv.lock").is_file() else "pip"


def agents_block(guides: Optional[List[Guide]] = None, toolchain: str = "uv") -> str:
    """The marked block :func:`sync` keeps in AGENTS.md, naming *toolchain*'s
    commands."""
    from operonx.cli.init import TOOLCHAINS

    guides = installed() if guides is None else guides
    names = ", ".join(f"{g.dist} {g.version}" for g in guides)
    t = TOOLCHAINS[toolchain]
    refresh = f"`{t['run']}operonx guide`"
    return (
        f"{AGENTS_BEGIN}\n"
        f"Installed: {names}. Read `.operonx/guide/README.md` first: it lists every\n"
        "page of every installed operonx package, each tested against that version.\n"
        f"Upgrade: `{t['upgrade']}`, then {refresh}\n"
        f"(after `{t['add']} operonx-agents` or `operonx-kb`, just {refresh}).\n"
        "Names come from variables: write `name=` only when other code reads the name,\n"
        "one op per line. Branch with `if_(cond, a).else_(b)` in the `>>` chain, never a\n"
        "hand-built `BranchOp`.\n"
        f"{AGENTS_END}"
    )


def commands_block(toolchain: str = "uv") -> str:
    """The marked block :func:`sync` keeps under AGENTS.md's "## Commands":
    ``operonx init``'s, naming *toolchain*'s commands."""
    from operonx.cli.init import COMMON, TEMPLATE_DIR, TOOLCHAINS, _align, _render

    text = (TEMPLATE_DIR / COMMON / "AGENTS.md.tmpl").read_text(encoding="utf-8")
    begin, end = text.index(COMMANDS_BEGIN), text.index(COMMANDS_END) + len(COMMANDS_END)
    return _align(_render(text[begin:end], TOOLCHAINS[toolchain]))


def _words(text: str) -> set:
    return {" ".join(line.split()) for line in text.splitlines() if line.strip()}


def _sync_commands(text: str, block: str) -> str:
    begin, end = text.find(COMMANDS_BEGIN), text.find(COMMANDS_END)
    if begin != -1 and end > begin:
        return text[:begin] + block + text[end + len(COMMANDS_END) :]
    # unmarked: an older init's section is replaced, never one someone added to
    found = _COMMANDS.search(text)
    if found is None or not _words(found.group(1)) or not _words(found.group(1)) <= _words(block):
        return text
    return text[: found.start(1)] + "\n" + block + "\n\n" + text[found.end(1) :]


def builder_block() -> str:
    """AGENTS.md's block on the BRD and the board, markers included."""
    return f"{BUILDER_BEGIN}\n{_BUILDER}\n{BUILDER_END}"


def _sync_builder(text: str, block: str, has_brd: bool) -> str:
    begin, end = text.find(BUILDER_BEGIN), text.find(BUILDER_END)
    if begin != -1 and end > begin:
        return text[:begin] + block + text[end + len(BUILDER_END) :]
    if not has_brd:
        return text
    at = text.find("\n## Commands\n")
    if at == -1:
        return text.rstrip("\n") + "\n\n" + block + "\n"
    return text[: at + 1] + block + "\n\n" + text[at + 1 :]


def _sync_agents_md(project: Path, block: str, commands: Optional[str] = None) -> bool:
    agents = project / "AGENTS.md"
    if not agents.is_file():
        return False
    text = agents.read_text(encoding="utf-8")
    begin, end = text.find(AGENTS_BEGIN), text.find(AGENTS_END)
    if begin != -1 and end > begin:
        new = text[:begin] + block + text[end + len(AGENTS_END) :]
    else:
        new = text.rstrip("\n") + "\n\n## operonx guides\n\n" + block + "\n"
    if commands is not None:
        new = _sync_commands(new, commands)
    new = _sync_builder(new, builder_block(), (project / "docs" / "brd").is_dir())
    if new == text:
        return False
    agents.write_text(new, encoding="utf-8")
    return True


def sync(project: Union[str, Path], toolchain: Optional[str] = None) -> List[str]:
    """Make ``<project>/.operonx/guide/`` the installed guides, and AGENTS.md's
    blocks name them and the commands, with *toolchain*'s (default: the project's,
    :func:`toolchain_of`). Returns what changed (:func:`changes`); running
    it twice changes nothing."""
    project = Path(project)
    toolchain = toolchain or toolchain_of(project)
    guides = installed()
    done = changes(project, guides)
    want = files(guides)
    dest = project / COPY_DIR
    if done:
        dest.mkdir(parents=True, exist_ok=True)
        for old in list(dest.iterdir()):  # the copy is generated: anything else goes
            keep = old.name in {g.name for g in guides} if old.is_dir() else old.name in want
            if not keep:
                shutil.rmtree(old) if old.is_dir() else old.unlink()
        for g in guides:
            folder = dest / g.name
            for old in folder.glob("*") if folder.is_dir() else ():
                if f"{g.name}/{old.name}" not in want:
                    shutil.rmtree(old) if old.is_dir() else old.unlink()
        for rel, data in want.items():
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.is_file() or target.read_bytes() != data:
                target.write_bytes(data)
    _sync_agents_md(project, agents_block(guides, toolchain), commands_block(toolchain))
    return done


def find_project(start: Optional[Path] = None) -> Optional[Path]:
    """The nearest directory at or above *start* holding ``operonx.toml``."""
    start = (start or Path.cwd()).resolve()
    for candidate in (start, *start.parents):
        if (candidate / "operonx.toml").is_file():
            return candidate
    return None
