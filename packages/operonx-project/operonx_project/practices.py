"""How a project is built: ``docs/brd/practices.toml`` (PROJECT_PRACTICES_PLAN D1).

The file is committed, so a team and a rebuild share it, and each card reads
the version on its own branch. Every section is optional; a project without
the file is built exactly as before. :func:`load` reads and validates it,
:func:`render` turns it into the instructions every coder session gets, and
:data:`PRESETS` are starting files (``operonx-project practices --init``).

- ``[tdd] mode``: ``acceptance`` (acceptance tests before the code, as before),
  ``strict`` (they must also fail before the code), ``off`` (no test author,
  no examiner);
- ``[architecture]``: a style named to every session, and the rules
  ``operonx-project arch`` checks on every round (layers, forbidden imports,
  private access, or a ``command`` for any other tool);
- ``[[contexts]]``: bounded contexts, reached from one another only through
  their public modules;
- ``[language] glossary``, ``[lessons] file``: files every session reads;
- ``[done] checks``: more commands every card must pass.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import tomllib
except ImportError:  # pragma: no cover - 3.10
    import tomli as tomllib

__all__ = ["FILE", "PRESETS", "Context", "Forbid", "Layer", "Practices", "PracticesError", "load", "render"]

#: Where the file lives, relative to the project's folder.
FILE = "docs/brd/practices.toml"
TDD_MODES = ("acceptance", "strict", "off")
STYLES = ("", "clean", "layered", "hexagonal", "none")
#: A file read into every session is cut here: rules, not a manual.
READ_LIMIT = 12_000


class PracticesError(ValueError):
    """The practices file is not valid; the message names the key."""


@dataclass
class Layer:
    name: str
    paths: List[str]


@dataclass
class Forbid:
    imports: List[str]
    layer: str = ""  # empty: every layer
    why: str = ""


@dataclass
class Context:
    """A bounded context: ``paths`` (or ``each``, one context per matching
    folder); another context imports it only where ``public`` matches and
    ``internal`` does not (globs relative to the context's folder)."""

    name: str
    paths: List[str] = field(default_factory=list)
    each: str = ""
    public: List[str] = field(default_factory=list)  # empty: every module
    internal: List[str] = field(default_factory=list)


@dataclass
class Practices:
    tdd: str = "acceptance"
    style: str = ""
    private: str = "allow"  # allow | forbid
    layers: List[Layer] = field(default_factory=list)
    forbid: List[Forbid] = field(default_factory=list)
    contexts: List[Context] = field(default_factory=list)
    #: tests, and terminal tools (as ``operonx lint`` exempts them), are not the product's architecture
    exclude: List[str] = field(default_factory=lambda: ["tests/**", "**/tests/**", "scripts/**", "bench/**"])
    command: str = ""  # any architecture tool, run as the same check
    glossary: str = ""
    lessons: str = ""
    done: List[Dict[str, str]] = field(default_factory=list)
    source: str = ""  # the file read, or "" when there is none

    @property
    def enforced(self) -> bool:
        """Is there an architecture check to run?"""
        return bool(self.layers or self.forbid or self.contexts or self.private == "forbid" or self.command)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _list(v: Any, key: str) -> List[str]:
    if v is None:
        return []
    if isinstance(v, str):
        return [v]
    if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
        raise PracticesError(f"{key} must be a list of strings")
    return list(v)


def _table(data: Dict[str, Any], key: str) -> Dict[str, Any]:
    v = data.get(key) or {}
    if not isinstance(v, dict):
        raise PracticesError(f"[{key}] must be a table")
    return v


def parse(data: Dict[str, Any], source: str = "") -> Practices:
    """Practices from the file's parsed TOML; :class:`PracticesError` names what is wrong."""
    known = {"tdd", "architecture", "contexts", "language", "lessons", "done"}
    extra = sorted(set(data) - known)
    if extra:
        raise PracticesError(f"unknown section(s) {extra}; known: {sorted(known)}")
    p = Practices(source=source)
    tdd = _table(data, "tdd")
    p.tdd = str(tdd.get("mode", "acceptance"))
    if p.tdd not in TDD_MODES:
        raise PracticesError(f"[tdd] mode must be one of {list(TDD_MODES)}, not {p.tdd!r}")
    arch = _table(data, "architecture")
    p.style = str(arch.get("style", ""))
    if p.style not in STYLES:
        raise PracticesError(f"[architecture] style must be one of {[s for s in STYLES if s]}, not {p.style!r}")
    p.private = str(arch.get("private", "allow"))
    if p.private not in ("allow", "forbid"):
        raise PracticesError(f"[architecture] private must be 'allow' or 'forbid', not {p.private!r}")
    for i, layer in enumerate(arch.get("layers") or []):
        if not isinstance(layer, dict) or not layer.get("name") or not layer.get("paths"):
            raise PracticesError(f"[architecture] layers[{i}] needs a name and paths")
        p.layers.append(Layer(str(layer["name"]), _list(layer["paths"], f"layers[{i}].paths")))
    names = [x.name for x in p.layers]
    if len(set(names)) != len(names):
        raise PracticesError(f"[architecture] layer names repeat: {names}")
    for i, f in enumerate(arch.get("forbid") or []):
        if not isinstance(f, dict) or not f.get("imports"):
            raise PracticesError(f"[architecture] forbid[{i}] needs imports")
        layer = str(f.get("layer", ""))
        if layer and layer not in names:
            raise PracticesError(f"[architecture] forbid[{i}] names layer {layer!r}, which is not declared")
        p.forbid.append(Forbid(_list(f["imports"], f"forbid[{i}].imports"), layer, str(f.get("why", ""))))
    if "exclude" in arch:
        p.exclude = _list(arch["exclude"], "[architecture] exclude")
    p.command = str(arch.get("command", ""))
    for i, c in enumerate(data.get("contexts") or []):
        if not isinstance(c, dict) or not c.get("name") or not (c.get("paths") or c.get("each")):
            raise PracticesError(f"[[contexts]] {i}: needs a name, and paths or each")
        p.contexts.append(Context(str(c["name"]), _list(c.get("paths"), "contexts.paths"), str(c.get("each", "")),
                                  _list(c.get("public"), "contexts.public"),
                                  _list(c.get("internal"), "contexts.internal")))  # fmt: skip
    p.glossary = str(_table(data, "language").get("glossary", ""))
    p.lessons = str(_table(data, "lessons").get("file", ""))
    for i, c in enumerate(_table(data, "done").get("checks") or []):
        if not isinstance(c, dict) or not c.get("name") or not c.get("cmd"):
            raise PracticesError(f"[done] checks[{i}] needs a name and a cmd")
        p.done.append({"name": str(c["name"]), "cmd": str(c["cmd"])})
    return p


def load(root: Path) -> Practices:
    """The project's practices (defaults when it has no file). Raises
    :class:`PracticesError` for a file that is not valid TOML or not valid."""
    path = Path(root) / FILE
    if not path.is_file():
        return Practices()
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as e:
        raise PracticesError(f"{FILE}: {e}") from e
    return parse(data, source=FILE)


def _read(root: Path, rel: str) -> str:
    try:
        text = (Path(root) / rel).read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return ""
    return text if len(text) <= READ_LIMIT else text[:READ_LIMIT] + "\n… (cut)"


_TDD = {
    "acceptance": "Acceptance tests for each card are written before its code, by a session of their own; the "
    "implementer makes them pass and may not change them.",
    "strict": "Test first, strictly: each code card's acceptance tests are written before its code and must fail "
    "before it (red), then pass (green). A test that already passes tests nothing new. Tests that pin behaviour "
    "that already works (a refactor's safety net, characterization tests) pass before and after by design: plan "
    "them as a card of kind test of their own, which the red check does not apply to.",
    "off": "No acceptance tests are written for a card: the repo's own checks and the review judge it, so a code "
    "change brings the tests that show it works.",
}
_STYLE = {
    "clean": "Clean Architecture: dependencies point inward, the domain knows nothing of frameworks, I/O or "
    "delivery, and the outer layers adapt to it.",
    "layered": "a layered architecture: each layer uses only the layers below it.",
    "hexagonal": "ports and adapters: the core defines ports (interfaces); adapters at the edge implement them.",
}


def render(p: Practices, root: Optional[Path] = None) -> str:
    """The practices as instructions for a coder session; "" when the project declares none."""
    if not p.source:
        return ""
    lines = [f"How this project is built ({FILE}; the engine checks what it can):", f"- Tests: {_TDD[p.tdd]}"]
    if p.style in _STYLE:
        lines.append(f"- Architecture: {_STYLE[p.style]}")
    if p.layers:
        chain = " → ".join(f"{x.name} ({', '.join(x.paths)})" for x in p.layers)
        lines.append(f"- Layers, outermost first: {chain}. A module imports only its own layer and the layers "
                     "after it, never one before it.")  # fmt: skip
    for f in p.forbid:
        who = f"The {f.layer} layer never imports" if f.layer else "No module imports"
        lines.append(f"- {who} {', '.join(f.imports)}" + (f": {f.why}." if f.why else "."))
    if p.private == "forbid":
        lines.append("- Public APIs only: never import another package's _private names, and never read or set a "
                     "_private attribute on an object your code does not define. Where the public API cannot do "
                     "it, ask.")  # fmt: skip
    for c in p.contexts:
        where = f"each folder {c.each}" if c.each else ", ".join(c.paths)
        reach = ""
        if c.public:
            reach = f" only through {', '.join(c.public)}"
        if c.internal:
            reach += f"{' and' if reach else ''} never through {', '.join(c.internal)}"
        lines.append(f"- Bounded context {c.name} ({where}): other contexts reach it{reach or ' through any module'}; "
                     "keep its model inside it.")  # fmt: skip
    if p.enforced:
        lines.append("- The engine's architecture check runs these rules on every round: a new violation fails the "
                     "round, as a failing test does.")  # fmt: skip
    if p.done:
        lines.append(f"- Done means these pass too: {', '.join(c['name'] + ' (' + c['cmd'] + ')' for c in p.done)}.")
    out = "\n".join(lines)
    if root is not None and p.glossary:
        terms = _read(root, p.glossary)
        if terms:
            out += f"\n\nThe project's language ({p.glossary}): name things with these terms.\n\n{terms}"
    if root is not None and p.lessons:
        lessons = _read(root, p.lessons)
        if any(line.startswith("- ") for line in lessons.splitlines()):  # a file with its header only says nothing
            out += (f"\n\nLessons from earlier milestones ({p.lessons}): each cost a round once; do not repeat "
                    f"them.\n\n{lessons}")  # fmt: skip
    return out


_COMMON = """
[language]
glossary = "docs/brd/GLOSSARY.md"   # its terms go to every session

[lessons]
file = "docs/brd/LESSONS.md"        # what each milestone taught; every session reads it

[done]
checks = []                         # more checks every card must pass: { name = "types", cmd = "uv run mypy src" }
"""

#: Starting files. A preset is a beginning: edit the file for the project.
PRESETS: Dict[str, str] = {
    "operonx": """# How this project is built. Every section is optional; `operonx-project practices` checks the file.
# This preset is the layout AGENTS.md states, checked: `operonx-project arch`.

[tdd]
mode = "acceptance"   # acceptance | strict (tests must fail before the code) | off

[architecture]
style = "layered"
private = "forbid"    # another package's _private names and attributes: never
layers = [            # outermost first: a module imports its own layer and the ones after it
  { name = "app",     paths = ["app/**"] },
  { name = "graph",   paths = ["src/**/graph.py"] },
  { name = "ops",     paths = ["src/**/ops.py"] },
  { name = "helpers", paths = ["src/**/_*.py"] },
]

# Bounded contexts (DDD), when the project has more than one domain: another context is reached only
# through its public modules. A feature's graph may use another feature's graph as a subgraph.
# [[contexts]]
# name = "billing"
# paths = ["src/billing/**"]
# public = ["graph.py"]
"""
    + _COMMON,
    "clean": """# How this project is built. Every section is optional; `operonx-project practices` checks the file.
# Clean Architecture for a Python package laid out as src/<pkg>/{domain,application,adapters,infrastructure}/.

[tdd]
mode = "strict"       # acceptance | strict (tests must fail before the code) | off

[architecture]
style = "clean"
private = "forbid"
layers = [            # outermost first: a module imports its own layer and the ones after it
  { name = "infrastructure", paths = ["src/**/infrastructure/**"] },
  { name = "adapters",       paths = ["src/**/adapters/**"] },
  { name = "application",    paths = ["src/**/application/**"] },
  { name = "domain",         paths = ["src/**/domain/**"] },
]

[[architecture.forbid]]   # the domain is plain Python
layer = "domain"
imports = ["sqlalchemy", "sqlalchemy.*", "fastapi", "fastapi.*", "requests", "requests.*", "httpx", "httpx.*",
           "django", "django.*", "flask", "flask.*", "operonx", "operonx.*"]
why = "the domain is plain Python: no framework, no I/O"
"""
    + _COMMON,
}
