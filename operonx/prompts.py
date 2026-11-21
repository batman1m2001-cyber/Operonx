"""Prompts: a project's prompt texts, each a named file, asked for by name.

A prompt is ``prompts/<name>.md`` in the project — the text as the model
gets it, ``{{variable}}`` where something is filled in, ``{{> other}}``
where another prompt is included. Optional front matter (YAML between
``---`` lines) holds what is not text::

    ---
    description: The intake conversation's system prompt
    ---
    Bạn là chuyên viên của OneWealth… Khách hàng: {{customer_name}}

An op asks for it by name, when the graph is built::

    LLMOp.of(resource="assistant", prompt=prompt("intake.system"), customer_name=name)
    LLMOp.of(resource="assistant", prompt={"system": prompt("guard"), "user": "{question}"}, ...)
    Agent(name="declare", instructions=prompt("declare.agent"), ...)

and the run row of that op says which prompt it used, and which version
(``attrs.prompts``).

Versions and labels, as Langfuse has them, are files beside the text::

    prompts/intake.system.md               the working text
    prompts/.versions/intake.system/3.md   version 3, frozen when it was published
    prompts/labels.toml                    [intake.system]  production = 3

``prompt(name)`` gives the version the process's label points at
(``OPERONX_PROMPT_LABEL``, default ``production``); a prompt with no such
label gives its working text. So nothing changes for a project that never
publishes, and rolling back is moving a label.

Braces: only ``{{name}}`` is a variable. Any other brace is text — the JSON
a prompt shows the model needs no escaping, which is why this is not
``str.format``.
"""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

__all__ = [
    "PromptNotFound",
    "PromptText",
    "RenderedPrompt",
    "labels",
    "prompt",
    "prompt_names",
    "prompt_refs",
    "prompts_dir",
    "publish",
    "set_label",
    "versions",
]

#: The label a process serves when none is named.
DEFAULT_LABEL = "production"

_VAR = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
_INCLUDE = re.compile(r"\{\{>\s*([A-Za-z0-9_./-]+)\s*\}\}")
_NAME = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]*(/[A-Za-z0-9_][A-Za-z0-9_.-]*)*$")


class PromptNotFound(LookupError):
    """No such prompt (or version of it) in the project's ``prompts/``."""


class PromptText(str):
    """A prompt, loaded: usable wherever a string is, and it knows its name.

    As a ``str`` it is an ``LLMOp`` template: ``{{name}}`` became ``{name}``
    and every other brace is doubled, so ``prompt=`` formats it as written
    and a copy that lost this class (a checkpoint, a JSON round trip) still
    formats the same. ``text`` is the prompt as its file has it — what an
    agent's ``instructions`` are given — and ``render`` fills it in.
    """

    name: str
    version: Optional[int]
    label: Optional[str]
    hash: str
    text: str
    variables: Tuple[str, ...]
    meta: Dict[str, Any]

    def __new__(
        cls,
        text: str,
        *,
        name: str,
        version: Optional[int] = None,
        label: Optional[str] = None,
        meta: Optional[Dict[str, Any]] = None,
    ) -> "PromptText":
        self = super().__new__(cls, _as_template(text))
        self.name = name
        self.version = version
        self.label = label
        self.text = text
        self.hash = hashlib.sha1(text.encode("utf-8")).hexdigest()[:10]
        self.variables = tuple(dict.fromkeys(_VAR.findall(text)))
        self.meta = dict(meta or {})
        return self

    @property
    def ref(self) -> Dict[str, Any]:
        """What a run records of this prompt: its name, version (``None``
        for the working text), the label that chose it, and its text's hash."""
        return {"name": self.name, "version": self.version, "label": self.label, "hash": self.hash}

    def render(self, **variables: Any) -> "RenderedPrompt":
        """The text with every ``{{name}}`` filled in from *variables*.

        The result is a string that remembers what it was made from
        (:class:`RenderedPrompt`): this prompt, and any prompt among the
        values. An ``LLMOp`` given it as an input records those on its run
        row, the same as a prompt named in ``prompt=``.
        """
        missing = [v for v in self.variables if v not in variables]
        if missing:
            raise KeyError(f"prompt {self.name!r}: no value for {', '.join(missing)}")
        refs = [self.ref]
        for name in self.variables:
            refs += _refs_in(variables[name])
        text = _VAR.sub(lambda m: _plain(variables[m.group(1)]), self.text)
        return RenderedPrompt(text, refs=refs)

    # A deep copy or a pickle (a graph is copied when it is compiled; a trace
    # crosses a process) keeps what it is.
    def __reduce__(self) -> Tuple[Any, ...]:
        return (_rebuild, (self.text, self.name, self.version, self.label, self.meta))

    def __deepcopy__(self, memo: Dict[int, Any]) -> "PromptText":
        return self

    def __copy__(self) -> "PromptText":
        return self


class RenderedPrompt(str):
    """A prompt with its variables filled in: the text, and the prompts it
    was made from (``refs`` — what a run records, outermost first)."""

    refs: Tuple[Dict[str, Any], ...]

    def __new__(cls, text: str, *, refs: Any = ()) -> "RenderedPrompt":
        self = super().__new__(cls, text)
        seen: List[Dict[str, Any]] = []
        for ref in refs:
            if ref not in seen:
                seen.append(dict(ref))
        self.refs = tuple(seen)
        return self

    def __reduce__(self) -> Tuple[Any, ...]:
        return (_rebuild_rendered, (str(self), self.refs))

    def __deepcopy__(self, memo: Dict[int, Any]) -> "RenderedPrompt":
        return self

    def __copy__(self) -> "RenderedPrompt":
        return self


def _rebuild_rendered(text: str, refs: Any) -> RenderedPrompt:
    return RenderedPrompt(text, refs=refs)


def _plain(value: Any) -> str:
    """*value* as the text a prompt takes it as: a prompt gives its text as written."""
    return value.text if isinstance(value, PromptText) else str(value)


def _refs_in(value: Any) -> List[Dict[str, Any]]:
    """The prompts a value came from: itself, or the ones it was rendered from."""
    if isinstance(value, PromptText):
        return [value.ref]
    if isinstance(value, RenderedPrompt):
        return list(value.refs)
    return []


def _rebuild(
    text: str, name: str, version: Optional[int], label: Optional[str], meta: Dict[str, Any]
) -> PromptText:
    return PromptText(text, name=name, version=version, label=label, meta=meta)


def _as_template(text: str) -> str:
    """*text* as a ``str.format`` template: ``{{name}}`` is ``{name}``, any other brace is literal."""
    out: List[str] = []
    at = 0
    for m in _VAR.finditer(text):
        out.append(text[at : m.start()].replace("{", "{{").replace("}", "}}"))
        out.append("{" + m.group(1) + "}")
        at = m.end()
    out.append(text[at:].replace("{", "{{").replace("}", "}}"))
    return "".join(out)


# ── where the prompts are ────────────────────────────────────────────────


def prompts_dir(root: Optional[Path] = None) -> Path:
    """The project's ``prompts/`` directory (it need not exist).

    *root* is the project; without one, the project this process runs in
    (the root an ``Application`` set, else the nearest ``operonx.toml`` at
    or above the working directory), else the working directory.
    """
    if root is None:
        from operonx.core.workflow_trace import active_project

        root = active_project() or Path.cwd()
    return Path(root) / "prompts"


def prompt_names(root: Optional[Path] = None) -> List[str]:
    """Every prompt the project has, by name, sorted."""
    base = prompts_dir(root)
    if not base.is_dir():
        return []
    names = []
    for path in base.rglob("*.md"):
        rel = path.relative_to(base)
        if any(part.startswith(".") for part in rel.parts):
            continue  # .versions
        names.append(rel.with_suffix("").as_posix())
    return sorted(names)


def _split(raw: str) -> Tuple[Dict[str, Any], str]:
    """Front matter and body of a prompt file. A file without ``---`` is all body."""
    if not raw.startswith("---"):
        return {}, raw
    head, sep, body = raw[3:].partition("\n---")
    if not sep:
        return {}, raw
    try:
        import yaml

        meta = yaml.safe_load(head) or {}
    except Exception:  # noqa: BLE001 — front matter that is not YAML is no front matter
        return {}, raw
    if not isinstance(meta, dict):
        return {}, raw
    return meta, body.split("\n", 1)[1] if "\n" in body else ""


def labels(root: Optional[Path] = None) -> Dict[str, Dict[str, int]]:
    """``prompts/labels.toml`` as ``{prompt name: {label: version}}``; empty when there is none."""
    path = prompts_dir(root) / "labels.toml"
    if not path.is_file():
        return {}
    try:
        import tomllib
    except ModuleNotFoundError:  # Python 3.10
        import tomli as tomllib  # type: ignore[no-redef]
    got = tomllib.loads(path.read_text(encoding="utf-8"))
    out: Dict[str, Dict[str, int]] = {}

    def walk(table: Dict[str, Any], prefix: str) -> None:
        # [intake.system] is a nested table in TOML: a table whose values are
        # all numbers is one prompt's labels, anything else is a name's prefix
        if table and all(isinstance(v, int) and not isinstance(v, bool) for v in table.values()):
            out[prefix] = dict(table)
            return
        for key, value in table.items():
            if isinstance(value, dict):
                walk(value, f"{prefix}.{key}" if prefix else key)

    walk(got, "")
    return out


def _read(base: Path, name: str, version: Optional[int]) -> str:
    if not _NAME.match(name):
        raise PromptNotFound(
            f"{name!r} is not a prompt name (letters, digits, . _ - and / for folders)"
        )
    path = base / f"{name}.md" if version is None else base / ".versions" / name / f"{version}.md"
    if not path.is_file():
        if version is not None:
            raise PromptNotFound(f"prompt {name!r} has no version {version} ({path} is missing)")
        have = prompt_names(base.parent)
        hint = f"; this project has: {', '.join(have)}" if have else f"; {base} has no prompts"
        raise PromptNotFound(f"no prompt named {name!r} ({path} is missing){hint}")
    return path.read_text(encoding="utf-8")


def prompt(
    name: str,
    *,
    version: Optional[int] = None,
    label: Optional[str] = None,
    root: Optional[Path] = None,
) -> PromptText:
    """The project's prompt *name*, ready to hand to an op.

    Without *version*, the version *label* points at — *label* defaults to
    ``OPERONX_PROMPT_LABEL``, else ``production`` — and, when the prompt has
    no such label, its working text. Raises :class:`PromptNotFound` for a
    name the project does not have, so a misspelt name fails when the graph
    is built (``operonx check``), not on a call.
    """
    base = prompts_dir(root)
    chosen = label or os.environ.get("OPERONX_PROMPT_LABEL") or DEFAULT_LABEL
    used: Optional[str] = None
    if version is None:
        version = labels(base.parent).get(name, {}).get(chosen)
        used = chosen if version is not None else None
    meta, body = _split(_read(base, name, version))
    body = _include(base, body, chosen, (name,))
    return PromptText(body.strip("\n"), name=name, version=version, label=used, meta=meta)


def _include(base: Path, body: str, label: str, seen: Tuple[str, ...]) -> str:
    """*body* with each ``{{> other}}`` replaced by that prompt's text (at the same label)."""

    def put(m: "re.Match[str]") -> str:
        other = m.group(1)
        if other in seen:
            raise PromptNotFound(f"prompts include each other: {' → '.join((*seen, other))}")
        version = labels(base.parent).get(other, {}).get(label)
        _, text = _split(_read(base, other, version))
        return _include(base, text, label, (*seen, other)).strip("\n")

    return _INCLUDE.sub(put, body)


def prompt_refs(value: Any) -> List[Dict[str, Any]]:
    """The prompts in *value* — a ``prompt=`` template (a string or a
    ``{system, user}`` dict) or an agent's instructions — as what a run
    records: each :attr:`PromptText.ref` (a rendered prompt gives the ones
    it was made from), with the ``part`` it fills when there are parts."""
    if isinstance(value, (PromptText, RenderedPrompt)):
        return _refs_in(value)
    if isinstance(value, dict):
        return [{"part": k, **ref} for k, v in value.items() for ref in _refs_in(v)]
    return []


# ── versions and labels ──────────────────────────────────────────────────


def versions(name: str, root: Optional[Path] = None) -> List[int]:
    """The published versions of *name*, oldest first."""
    d = prompts_dir(root) / ".versions" / name
    if not d.is_dir():
        return []
    return sorted(int(p.stem) for p in d.glob("*.md") if p.stem.isdigit())


def publish(name: str, root: Optional[Path] = None) -> Tuple[int, bool]:
    """Freeze the working text of *name* as its next version.

    Returns ``(version, new)``: a text identical to the newest version is
    not published again (``new`` is False and that version is returned).
    A version is the whole file, front matter included, and is never edited.
    """
    base = prompts_dir(root)
    raw = _read(base, name, None)
    have = versions(name, base.parent)
    if have and _read(base, name, have[-1]) == raw:
        return have[-1], False
    number = (have[-1] + 1) if have else 1
    target = base / ".versions" / name / f"{number}.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(raw, encoding="utf-8")
    return number, True


def set_label(name: str, label: str, version: int, root: Optional[Path] = None) -> Optional[int]:
    """Point *label* of *name* at *version*; returns the version it pointed at before."""
    base = prompts_dir(root)
    if not re.match(r"^[A-Za-z][A-Za-z0-9_-]*$", label):
        raise ValueError(f"{label!r} is not a label name (letters, digits, _ and -)")
    if version not in versions(name, base.parent):
        raise PromptNotFound(
            f"prompt {name!r} has no version {version}; published: "
            f"{', '.join(map(str, versions(name, base.parent))) or 'none'}"
        )
    table = labels(base.parent)
    before = table.get(name, {}).get(label)
    table.setdefault(name, {})[label] = version
    lines = [
        "# Which version of each prompt a label serves (operonx prompt label NAME LABEL VERSION).",
        "# A process serves the label in OPERONX_PROMPT_LABEL, else `production`; a prompt",
        "# without that label is served from its working text.",
    ]
    for prompt_name in sorted(table):
        lines += ["", f'["{prompt_name}"]']
        lines += [f"{k} = {v}" for k, v in sorted(table[prompt_name].items())]
    (base / "labels.toml").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return before
