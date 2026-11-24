"""Prompts: a project's prompts, each an object with a name and an id.

There are two kinds of object.

A **text object** is ``prompts/<name>.md`` — the text as the model gets it,
``{{variable}}`` where data is filled in. Optional front matter (YAML
between ``---`` lines) holds what is not text::

    ---
    id: pr_9e02c1a4
    description: The intake conversation's system prompt
    ---
    Bạn là chuyên viên của OneWealth… Khách hàng: {{customer_name}}

A **composite object** is ``prompts/<name>.yaml`` — an id and ordered
links to other objects, never text of its own beyond short separators::

    id: pr_c1a5d0e2
    kind: chat                  # system/user parts; `kind: text` has `parts:`
    system:
      - pick: agent_name        # one object, chosen by a data value
        cases: {educa_hr: educa_hr/analyzer}
        else: none
      - var: intent_definitions # data from code, as a whole part
      - when: voice             # linked only when the value is true
        use: assistant/voice
      - use: classifier/rules   # always linked
    user:
      - use: classifier/user

``cache: system`` on a chat object sends its system message as a cached
prefix (one text block marked ``cache_control: ephemeral``), for a provider
that caches what is marked; the text is the same.

Parts are joined by ``join`` (default a blank line); a part that renders
empty is left out, separators and all. ``{{x}}`` is always data, and a
prompt is never a variable's value: link it with ``use``/``pick``/``when``.

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

One process can try one prompt's version while every other prompt keeps its
label: ``OPERONX_PROMPT_PINS="classifier=4,intake.system=2"`` pins those names
to those versions (a composite's linked objects are pinned by their own names).

A running service was built from files as they were. With a prompt store set
(``[prompts] store`` in ``operonx.toml`` — a Postgres address), published
versions and labels are also copied there and ``prompt(name)`` serves what the store's label
points at — :mod:`operonx.prompt_store`. The files stay the one place a
prompt is written.

Braces: only ``{{name}}`` is a variable. Any other brace is text — the JSON
a prompt shows the model needs no escaping, which is why this is not
``str.format``.
"""

from __future__ import annotations

import hashlib
import os
import re
import secrets
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

__all__ = [
    "CompositePrompt",
    "PromptFormatError",
    "PromptNotFound",
    "PromptText",
    "RenderedChat",
    "RenderedPrompt",
    "ensure_id",
    "move",
    "new_id",
    "labels",
    "pins",
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
#: ``{{> other}}`` — the include that composites replaced; refused when a text loads.
_INCLUDE = re.compile(r"\{\{>\s*([^}]*?)\s*\}\}")
_NAME = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]*(/[A-Za-z0-9_][A-Za-z0-9_.-]*)*$")
_ID = re.compile(r"^pr_[0-9a-f]{8}$")
#: The file kinds a prompt can be: a text object, a composite.
_SUFFIXES = (".md", ".yaml")


#: Read the files only, never the prompt store (lint, which reads source only).
_FILES_ONLY: ContextVar[bool] = ContextVar("operonx_prompts_files_only", default=False)


def new_id() -> str:
    """A fresh prompt id: ``pr_`` and 8 hex digits. Minted once, never changed."""
    return "pr_" + secrets.token_hex(4)


class PromptNotFound(LookupError):
    """No such prompt (or version of it) in the project's ``prompts/``."""


class PromptFormatError(ValueError):
    """A composite prompt's file is not one: an unknown key, a part with no
    kind, a link to itself — said with the file's name and the part."""


class PromptText:
    """A text object, loaded: its name, id and version, and ``text`` — the
    prompt as its file has it (what an agent's ``instructions`` are given).

    It is not a string: ``LLMOp(prompt=…)`` fills its ``{{x}}`` from the op's
    values and leaves every other brace as written, ``render`` / ``fill`` do
    the same in code, and ``str()`` is the text as written.
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
        self = super().__new__(cls)
        self.name = name
        self.version = version
        self.label = label
        self.text = text
        self.hash = hashlib.sha1(text.encode("utf-8")).hexdigest()[:10]
        self.variables = tuple(dict.fromkeys(_VAR.findall(text)))
        self.meta = dict(meta or {})
        return self

    kind = "text"

    @property
    def id(self) -> Optional[str]:
        """The object's id (front matter ``id:``); None for a file that has none yet."""
        got = self.meta.get("id")
        return got if isinstance(got, str) else None

    @property
    def ref(self) -> Dict[str, Any]:
        """What a run records of this prompt: its name, version (``None``
        for the working text), the label that chose it, and its text's hash
        (and its id, when the file has one)."""
        ref = {"name": self.name, "version": self.version, "label": self.label, "hash": self.hash}
        if self.id:
            ref["id"] = self.id
        return ref

    def __str__(self) -> str:
        return self.text

    def __repr__(self) -> str:
        return f"<PromptText {self.name!r}>"

    def __eq__(self, other: Any) -> bool:
        return isinstance(other, PromptText) and (self.name, self.version, self.text) == (
            other.name,
            other.version,
            other.text,
        )

    def __hash__(self) -> int:
        return hash((self.name, self.version, self.hash))

    def fill(self, data: Dict[str, Any]) -> str:
        """The text with its ``{{x}}`` filled from *data* — strict: a prompt
        as a value is refused (link it in a composite)."""
        missing = [v for v in self.variables if v not in data]
        if missing:
            raise KeyError(f"prompt {self.name!r}: no value for {', '.join(missing)}")
        for v in self.variables:
            _not_a_prompt(data[v], v, self.name)
        return _VAR.sub(lambda m: str(data[m.group(1)]), self.text)

    def render(self, **variables: Any) -> "RenderedPrompt":
        """The text with every ``{{name}}`` filled in from *variables*.

        The result is a string that remembers what it was made from
        (:class:`RenderedPrompt`): this prompt. An ``LLMOp`` given it as an
        input records it on its run row, the same as a prompt named in
        ``prompt=``. A prompt as a value is refused — link it in a composite.
        """
        missing = [v for v in self.variables if v not in variables]
        if missing:
            raise KeyError(f"prompt {self.name!r}: no value for {', '.join(missing)}")
        return RenderedPrompt(self.fill(variables), refs=[self.ref])

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
    tree: Optional[Dict[str, Any]]

    def __new__(
        cls, text: str, *, refs: Any = (), tree: Optional[Dict[str, Any]] = None
    ) -> "RenderedPrompt":
        self = super().__new__(cls, text)
        seen: List[Dict[str, Any]] = []
        for ref in refs:
            if ref not in seen:
                seen.append(dict(ref))
        self.refs = tuple(seen)
        self.tree = tree
        return self

    def __reduce__(self) -> Tuple[Any, ...]:
        return (_rebuild_rendered, (str(self), self.refs, self.tree))

    def __deepcopy__(self, memo: Dict[int, Any]) -> "RenderedPrompt":
        return self

    def __copy__(self) -> "RenderedPrompt":
        return self


def _rebuild_rendered(text: str, refs: Any, tree: Any = None) -> RenderedPrompt:
    return RenderedPrompt(text, refs=refs, tree=tree)


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


# ── composite objects ────────────────────────────────────────────────────

_PART_KINDS = ("use", "pick", "when", "var", "text")
_PART_KEYS = {
    "use": {"use"},
    "pick": {"pick", "cases", "else"},
    "when": {"when", "use"},
    "var": {"var"},
    "text": {"text"},
}
_ROLES = ("system", "user")
_TOP_KEYS = {"id", "kind", "description", "join", "cache", "parts", *_ROLES}
_NO_DEFAULT = object()


def _not_a_prompt(value: Any, var: str, owner: str) -> None:
    if isinstance(value, (PromptText, CompositePrompt)) or (
        isinstance(value, RenderedPrompt) and value.refs
    ):
        raise TypeError(
            f"prompt {owner!r}: {var!r} was given a prompt, and a variable is data. "
            f"Link it in the composite instead (use: / pick: / when:)."
        )


class _Part:
    """One part of a composite: a link (use / pick / when), data (var) or a literal (text)."""

    __slots__ = ("kind", "var", "target", "cases", "default", "text")

    def __init__(
        self,
        kind: str,
        *,
        var: str = "",
        target: Any = None,
        cases: Optional[Dict[str, Any]] = None,
        default: Any = _NO_DEFAULT,
        text: str = "",
    ):
        self.kind = kind
        self.var = var
        self.target = target
        self.cases = cases or {}
        self.default = default
        self.text = text

    def objects(self) -> List[Any]:
        """The objects this part can link (pick: every case, and else)."""
        if self.kind in ("use", "when"):
            return [self.target]
        if self.kind == "pick":
            got = list(self.cases.values())
            if self.default is not _NO_DEFAULT:
                got.append(self.default)
            return [o for o in got if o is not None]
        return []


class CompositePrompt:
    """A prompt made of links: ``prompts/<name>.yaml`` (see the module docstring).

    ``kind`` is ``chat`` (``system``/``user`` parts, rendered to a
    :class:`RenderedChat`) or ``text`` (``parts``, rendered to a
    :class:`RenderedPrompt`). ``self["system"]`` is one role of a chat
    object, as a text composite with the same name and id.
    """

    def __init__(
        self,
        *,
        name: str,
        kind: str,
        roles: Dict[str, List[_Part]],
        join: str,
        meta: Dict[str, Any],
        version: Optional[int],
        label: Optional[str],
        raw: str,
        role: Optional[str] = None,
        cache: Optional[str] = None,
    ):
        self.name = name
        self.cache = cache
        self.kind = kind
        self.roles = roles
        self.join = join
        self.meta = meta
        self.version = version
        self.label = label
        self.raw = raw
        self.role = role
        self.hash = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:10]

    @property
    def id(self) -> Optional[str]:
        got = self.meta.get("id")
        return got if isinstance(got, str) else None

    @property
    def ref(self) -> Dict[str, Any]:
        ref = {"name": self.name, "version": self.version, "label": self.label, "hash": self.hash}
        if self.id:
            ref["id"] = self.id
        if self.role:
            ref["part"] = self.role
        return ref

    @property
    def text(self) -> str:
        """The file as written (what a version freezes)."""
        return self.raw

    def __getitem__(self, role: str) -> "CompositePrompt":
        if self.kind != "chat" or role not in self.roles:
            raise KeyError(
                f"prompt {self.name!r} has no {role!r} part (it has: {', '.join(self.roles)})"
            )
        return CompositePrompt(
            name=self.name,
            kind="text",
            roles={"parts": self.roles[role]},
            join=self.join,
            meta=self.meta,
            version=self.version,
            label=self.label,
            raw=self.raw,
            role=role,
        )

    # ── what it is made of ──
    def links(self) -> List[Dict[str, Any]]:
        """Every link in this object, in order: ``{role, how, var, case, name, kind}``
        (``how`` is use / pick / when; a pick has one row per case, ``case``
        is ``else`` for the default). One level: a linked composite has its own."""
        out = []
        for role, parts in self.roles.items():
            for part in parts:
                if part.kind in ("use", "when"):
                    out.append(
                        {
                            "role": role,
                            "how": part.kind,
                            "var": part.var or None,
                            "case": None,
                            "name": part.target.name,
                            "kind": part.target.kind,
                        }
                    )
                elif part.kind == "pick":
                    for case, obj in part.cases.items():
                        out.append(
                            {
                                "role": role,
                                "how": "pick",
                                "var": part.var,
                                "case": case,
                                "name": obj.name if obj is not None else None,
                                "kind": obj.kind if obj is not None else None,
                            }
                        )
                    if part.default is not _NO_DEFAULT:
                        obj = part.default
                        out.append(
                            {
                                "role": role,
                                "how": "pick",
                                "var": part.var,
                                "case": "else",
                                "name": obj.name if obj is not None else None,
                                "kind": obj.kind if obj is not None else None,
                            }
                        )
        return out

    @property
    def variables(self) -> Dict[str, str]:
        """Each variable rendering needs, and where it comes from: ``pick`` /
        ``when`` (it chooses a link), ``data`` (a ``var:`` part of this
        object), or the name of the linked text object whose ``{{x}}`` it is."""
        out: Dict[str, str] = {}

        def walk(obj: Any, seen: Tuple[str, ...]) -> None:
            if isinstance(obj, PromptText):
                for v in obj.variables:
                    out.setdefault(v, obj.name)
                return
            for parts in obj.roles.values():
                for part in parts:
                    if part.kind in ("pick", "when"):
                        out.setdefault(part.var, part.kind)
                    elif part.kind == "var":
                        out.setdefault(part.var, "data")
                    for linked in part.objects():
                        if linked.name not in seen:
                            walk(linked, (*seen, linked.name))

        walk(self, (self.name,))
        return out

    # ── rendering ──
    def render(self, **data: Any) -> Any:
        """Text, made only here: a :class:`RenderedChat` for a chat object
        (``.system``, ``.user``), else a :class:`RenderedPrompt`. Both carry
        ``tree`` — the ids and versions of every object it linked, and what
        each ``pick`` chose — and ``refs``, the same flattened."""
        if self.kind == "chat":
            got = {role: self[role].render(**data) for role in self.roles}
            return RenderedChat(got, name=self.name, ref=self.ref, cache=self.cache)
        refs: List[Dict[str, Any]] = [self.ref]
        text, nodes = _render_parts(self, self.roles["parts"], self.join, data, refs)
        return RenderedPrompt(
            text, refs=refs, tree={**self.ref, "kind": "composite", "parts": nodes}
        )

    def __repr__(self) -> str:
        part = f"[{self.role!r}]" if self.role else ""
        return f"<CompositePrompt {self.name!r}{part} {self.kind} v{self.version}>"

    # a graph is deep-copied when it is compiled: the object is immutable
    def __deepcopy__(self, memo: Dict[int, Any]) -> "CompositePrompt":
        return self

    def __copy__(self) -> "CompositePrompt":
        return self


class RenderedChat:
    """A chat composite, rendered: ``system`` and ``user`` (each a
    :class:`RenderedPrompt`), ``messages`` as a model takes them, ``tree``
    and ``refs`` as a run row records them. ``cache`` names the role sent as
    a cached prefix (``cache: system`` in the file), or is None."""

    def __init__(
        self,
        roles: Dict[str, RenderedPrompt],
        *,
        name: str,
        ref: Dict[str, Any],
        cache: Optional[str] = None,
    ):
        self.roles = roles
        self.cache = cache
        self.name = name
        self.system = roles.get("system")
        self.user = roles.get("user")
        refs: List[Dict[str, Any]] = [ref]
        for role, got in roles.items():
            # each role is rendered as this object's own text composite: its first ref is ``ref``
            refs += [{"part": role, **r} if "part" not in r else r for r in got.refs[1:]]
        self.refs = tuple(refs)
        self.tree = {
            **ref,
            "kind": "composite",
            "parts": [{"role": role, **(got.tree or {})} for role, got in roles.items()],
        }

    @property
    def messages(self) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for role, text in self.roles.items():
            if role == self.cache:
                block = {"type": "text", "text": str(text), "cache_control": {"type": "ephemeral"}}
                out.append({"role": role, "content": [block]})
            else:
                out.append({"role": role, "content": str(text)})
        return out


def _render_parts(
    owner: CompositePrompt,
    parts: List[_Part],
    join: str,
    data: Dict[str, Any],
    refs: List[Dict[str, Any]],
) -> Tuple[str, List[Dict[str, Any]]]:
    out: List[str] = []
    nodes: List[Dict[str, Any]] = []

    def need(var: str) -> Any:
        if var not in data:
            raise KeyError(f"prompt {owner.name!r}: no value for {var}")
        return data[var]

    for part in parts:
        piece = ""
        if part.kind == "text":
            piece = part.text
        elif part.kind == "var":
            value = need(part.var)
            _not_a_prompt(value, part.var, owner.name)
            piece = "" if value is None else str(value)
            nodes.append({"var": part.var})
        elif part.kind == "use":
            piece, node = _render_object(part.target, data, refs)
            nodes.append({"use": node})
        elif part.kind == "when":
            on = bool(need(part.var))
            node = None
            if on:
                piece, node = _render_object(part.target, data, refs)
            nodes.append({"when": part.var, "value": on, "use": node})
        elif part.kind == "pick":
            value = need(part.var)
            key = _case_key(value)
            if key in part.cases:
                chosen = part.cases[key]
            elif part.default is not _NO_DEFAULT:
                chosen = part.default
            else:
                raise KeyError(
                    f"prompt {owner.name!r}: pick {part.var}={value!r} has no case "
                    f"(cases: {', '.join(part.cases)}; add `else:` for the rest)"
                )
            node = None
            if chosen is not None:
                piece, node = _render_object(chosen, data, refs)
            nodes.append({"pick": part.var, "value": key, "use": node})
        if piece:
            out.append(piece)
    return join.join(out), nodes


def _render_object(
    obj: Any, data: Dict[str, Any], refs: List[Dict[str, Any]]
) -> Tuple[str, Dict[str, Any]]:
    if isinstance(obj, PromptText):
        refs.append(obj.ref)
        return obj.fill(data), {**obj.ref, "kind": "text"}
    refs.append(obj.ref)
    text, nodes = _render_parts(obj, obj.roles["parts"], obj.join, data, refs)
    return text, {**obj.ref, "kind": "composite", "parts": nodes}


def _case_key(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _composite(
    base: Path,
    name: str,
    raw: str,
    version: Optional[int],
    used: Optional[str],
    label: str,
    seen: Tuple[str, ...],
) -> CompositePrompt:
    """Parse ``prompts/<name>.yaml`` and load every object it links (at the same label)."""
    import yaml

    where = f"prompts/{name}.yaml" + (f" v{version}" if version is not None else "")
    try:
        doc = yaml.safe_load(raw) or {}
    except yaml.YAMLError as exc:
        raise PromptFormatError(f"{where}: not YAML ({exc})") from exc
    if not isinstance(doc, dict):
        raise PromptFormatError(f"{where}: a composite is a mapping (id, kind, parts…)")
    unknown = set(doc) - _TOP_KEYS
    if unknown:
        raise PromptFormatError(
            f"{where}: unknown key(s) {', '.join(sorted(unknown))}; "
            f"a composite has {', '.join(sorted(_TOP_KEYS))}"
        )
    kind = doc.get("kind", "text")
    if kind not in ("chat", "text"):
        raise PromptFormatError(f"{where}: kind is chat or text, not {kind!r}")
    role_keys = _ROLES if kind == "chat" else ("parts",)
    wrong = [k for k in doc if k in (*_ROLES, "parts") and k not in role_keys]
    if wrong:
        raise PromptFormatError(
            f"{where}: a {kind} composite has {' / '.join(role_keys)}, not {', '.join(wrong)}"
        )
    join = doc.get("join", "\n\n")
    if not isinstance(join, str):
        raise PromptFormatError(f"{where}: join is a string")
    cache = doc.get("cache")
    if cache is not None and (kind != "chat" or cache != "system"):
        raise PromptFormatError(
            f"{where}: cache is `cache: system` on a chat composite (its system message, a cached prefix)"
        )

    def link(target: Any, at: str) -> Any:
        if target is None or (isinstance(target, str) and target.lower() == "none"):
            return None
        if not isinstance(target, str):
            raise PromptFormatError(f"{where} {at}: a link is a prompt name, not {target!r}")
        if target in seen:
            raise PromptFormatError(
                f"{where}: prompts link each other: {' → '.join((*seen, target))}"
            )
        return _load(base, target, None, label, (*seen, target))

    roles: Dict[str, List[_Part]] = {}
    for role in role_keys:
        items = doc.get(role)
        if items is None:
            continue
        if not isinstance(items, list):
            raise PromptFormatError(f"{where} {role}: a list of parts")
        parts: List[_Part] = []
        for i, item in enumerate(items, 1):
            at = f"{role}[{i}]"
            if isinstance(item, str):  # a bare name is a use
                item = {"use": item}
            if not isinstance(item, dict):
                raise PromptFormatError(
                    f"{where} {at}: a part is a mapping (use / pick / when / var / text)"
                )
            kinds = [k for k in ("pick", "when", "var", "text") if k in item] or (
                ["use"] if "use" in item else []
            )
            if len(kinds) != 1:
                raise PromptFormatError(
                    f"{where} {at}: one of use / pick / when / var / text, got {', '.join(item) or 'nothing'}"
                )
            pk = kinds[0]
            extra = set(item) - _PART_KEYS[pk]
            if extra:
                raise PromptFormatError(
                    f"{where} {at}: {pk} takes {', '.join(sorted(_PART_KEYS[pk]))}, not {', '.join(sorted(extra))}"
                )
            if pk == "text":
                parts.append(_Part("text", text=str(item["text"])))
            elif pk == "var":
                parts.append(_Part("var", var=_varname(item["var"], where, at)))
            elif pk == "use":
                obj = link(item["use"], at)
                if obj is None:
                    raise PromptFormatError(f"{where} {at}: use names a prompt")
                parts.append(_Part("use", target=obj))
            elif pk == "when":
                if "use" not in item:
                    raise PromptFormatError(f"{where} {at}: when needs use: <prompt>")
                obj = link(item["use"], at)
                if obj is None:
                    raise PromptFormatError(f"{where} {at}: use names a prompt")
                parts.append(_Part("when", var=_varname(item["when"], where, at), target=obj))
            else:
                cases = item.get("cases")
                if not isinstance(cases, dict) or not cases:
                    raise PromptFormatError(f"{where} {at}: pick needs cases: {{value: prompt}}")
                parts.append(
                    _Part(
                        "pick",
                        var=_varname(item["pick"], where, at),
                        cases={_case_key(k): link(v, f"{at} case {k}") for k, v in cases.items()},
                        default=link(item["else"], f"{at} else") if "else" in item else _NO_DEFAULT,
                    )
                )
        roles[role] = parts
    if not roles:
        raise PromptFormatError(f"{where}: no parts ({' / '.join(role_keys)})")
    meta = {k: v for k, v in doc.items() if k in ("id", "description")}
    return CompositePrompt(
        name=name,
        kind=kind,
        roles=roles,
        join=join,
        meta=meta,
        version=version,
        label=used,
        raw=raw,
        cache=cache,
    )


def _varname(value: Any, where: str, at: str) -> str:
    if not isinstance(value, str) or not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", value):
        raise PromptFormatError(f"{where} {at}: {value!r} is not a variable name")
    return value


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
    names = set()
    for suffix in _SUFFIXES:
        for path in base.rglob(f"*{suffix}"):
            rel = path.relative_to(base)
            if any(part.startswith(".") for part in rel.parts):
                continue  # .versions
            names.add(rel.with_suffix("").as_posix())
    return sorted(names)


def prompt_path(name: str, root: Optional[Path] = None) -> Path:
    """The file of prompt *name*: ``prompts/<name>.md`` (text) or ``.yaml`` (composite)."""
    return _path(prompts_dir(root), name)


def _path(base: Path, name: str) -> Path:
    if not _NAME.match(name):
        raise PromptNotFound(
            f"{name!r} is not a prompt name (letters, digits, . _ - and / for folders)"
        )
    found = [
        base / f"{name}{suffix}" for suffix in _SUFFIXES if (base / f"{name}{suffix}").is_file()
    ]
    if len(found) > 1:
        raise PromptFormatError(
            f"prompt {name!r} is both {found[0].name} and {found[1].name}: keep one"
        )
    return found[0] if found else base / f"{name}.md"


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
    path = _path(base, name)
    if version is not None:
        path = base / ".versions" / name / f"{version}{path.suffix}"
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
) -> Any:
    """The project's prompt *name*, ready to hand to an op: a
    :class:`PromptText` for ``<name>.md``, a :class:`CompositePrompt` for
    ``<name>.yaml`` (every object it links loaded with it, at the same label).

    Without *version*, the version ``OPERONX_PROMPT_PINS`` pins *name* to
    (:func:`pins`), else the one *label* points at — *label* defaults to
    ``OPERONX_PROMPT_LABEL``, else ``production`` — and, when the prompt has
    no such label, its working text. Raises :class:`PromptNotFound` for a
    name the project does not have (or a link to one), so a misspelt name
    fails when the graph is built (``operonx check``), not on a call.
    """
    base = prompts_dir(root)
    chosen = label or os.environ.get("OPERONX_PROMPT_LABEL") or DEFAULT_LABEL
    return _load(base, name, version, chosen, (name,))


def pins() -> Dict[str, int]:
    """``OPERONX_PROMPT_PINS`` as ``{name: version}``: ``"classifier=4,ask=2"``
    (commas or spaces between entries); ``{}`` when it is unset."""
    out: Dict[str, int] = {}
    for entry in re.split(r"[,\s]+", os.environ.get("OPERONX_PROMPT_PINS", "").strip()):
        if not entry:
            continue
        name, eq, number = entry.partition("=")
        if not eq or not number.strip().isdigit() or not name.strip():
            raise PromptFormatError(
                f"OPERONX_PROMPT_PINS: {entry!r} is not name=version (e.g. classifier=4)"
            )
        out[name.strip()] = int(number)
    return out


def _load(base: Path, name: str, version: Optional[int], chosen: str, seen: Tuple[str, ...]) -> Any:
    used: Optional[str] = None
    if version is None:
        version = pins().get(name)
    if version is not None:
        raw = _read(base, name, version)
    else:
        version, raw = _labelled(base, name, chosen)
        used = chosen if version is not None else None
    if _path(base, name).suffix == ".yaml":
        return _composite(base, name, raw, version, used, chosen, seen)
    meta, body = _split(raw)
    m = _INCLUDE.search(body)
    if m:
        raise PromptFormatError(
            f"prompt {name!r}: {m.group(0)} is gone — a text object does not include another. "
            f"Link both from a composite: prompts/<name>.yaml with parts: [{{use: …}}, {{use: {m.group(1)}}}]"
        )
    return PromptText(body.strip("\n"), name=name, version=version, label=used, meta=meta)


def _labelled(base: Path, name: str, label: str) -> Tuple[Optional[int], str]:
    """``(version, file text)`` *label* serves for *name*: what the prompt
    store says when one is set and has that label (:mod:`operonx.prompt_store`
    — a version published since this checkout was built is served from
    there), else what the files say; ``(None, working text)`` for a prompt
    without the label."""
    from operonx import prompt_store

    if not _FILES_ONLY.get() and prompt_store.configured(base.parent):
        if not _NAME.match(name):
            _read(base, name, None)  # says why
        got = prompt_store.served(name, label, base.parent)
        if got is not None:
            return got
    version = labels(base.parent).get(name, {}).get(label)
    return version, _read(base, name, version)


def prompt_refs(value: Any) -> List[Dict[str, Any]]:
    """The prompts in *value* — a ``prompt=`` template (a string or a
    ``{system, user}`` dict) or an agent's instructions — as what a run
    records: each :attr:`PromptText.ref` (a rendered prompt gives the ones
    it was made from), with the ``part`` it fills when there are parts."""
    if isinstance(value, (PromptText, RenderedPrompt)):
        return _refs_in(value)
    if isinstance(value, CompositePrompt):
        return [value.ref]
    if isinstance(value, RenderedChat):
        return list(value.refs)
    if isinstance(value, dict):
        return [{"part": k, **ref} for k, v in value.items() for ref in _refs_in(v)]
    return []


# ── versions and labels ──────────────────────────────────────────────────


def versions(name: str, root: Optional[Path] = None) -> List[int]:
    """The published versions of *name*, oldest first."""
    base = prompts_dir(root)
    d = base / ".versions" / name
    if not d.is_dir():
        return []
    suffix = _path(base, name).suffix
    return sorted(int(p.stem) for p in d.glob(f"*{suffix}") if p.stem.isdigit())


def publish(name: str, root: Optional[Path] = None) -> Tuple[int, bool]:
    """Freeze the working text of *name* as its next version.

    Returns ``(version, new)``: a text identical to the newest version is
    not published again (``new`` is False and that version is returned).
    A version is the whole file, front matter included, and is never edited.
    """
    base = prompts_dir(root)
    ensure_id(name, base.parent)
    raw = _read(base, name, None)
    have = versions(name, base.parent)
    if have and _read(base, name, have[-1]) == raw:
        return have[-1], False
    number = (have[-1] + 1) if have else 1
    target = base / ".versions" / name / f"{number}{_path(base, name).suffix}"
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
    _write_labels(base, table)
    return before


def _write_labels(base: Path, table: Dict[str, Dict[str, int]]) -> None:
    lines = [
        "# Which version of each prompt a label serves (operonx prompt label NAME LABEL VERSION).",
        "# A process serves the label in OPERONX_PROMPT_LABEL, else `production`; a prompt",
        "# without that label is served from its working text.",
    ]
    for prompt_name in sorted(table):
        lines += ["", f'["{prompt_name}"]']
        lines += [f"{k} = {v}" for k, v in sorted(table[prompt_name].items())]
    (base / "labels.toml").write_text("\n".join(lines) + "\n", encoding="utf-8")


def ensure_id(name: str, root: Optional[Path] = None) -> Tuple[str, bool]:
    """The id of prompt *name*, minting one into its working file when it has
    none (front matter of a ``.md``, the first line of a ``.yaml``).
    Returns ``(id, written)``. Publishing calls this, so every version has one.
    A file that lost its id (rewritten whole) gets its last version's back."""
    base = prompts_dir(root)
    path = _path(base, name)
    raw = _read(base, name, None)
    have = versions(name, base.parent)
    if path.suffix == ".yaml":
        import yaml

        def id_in(text: str) -> Any:
            doc = yaml.safe_load(text) or {}
            return doc.get("id") if isinstance(doc, dict) else None

        got = id_in(raw)
        if isinstance(got, str) and _ID.match(got):
            return got, False
        if got is not None:
            raise PromptFormatError(f"prompts/{name}.yaml: id {got!r} is not pr_ and 8 hex digits")
        last = id_in(_read(base, name, have[-1])) if have else None
        new = last if isinstance(last, str) and _ID.match(last) else new_id()
        path.write_text(f"id: {new}\n{raw}", encoding="utf-8")
        return new, True
    meta, body = _split(raw)
    got = meta.get("id")
    if isinstance(got, str) and _ID.match(got):
        return got, False
    if got is not None:
        raise PromptFormatError(f"prompts/{name}.md: id {got!r} is not pr_ and 8 hex digits")
    last = _split(_read(base, name, have[-1]))[0].get("id") if have else None
    new = last if isinstance(last, str) and _ID.match(last) else new_id()
    if raw.startswith("---") and meta:
        path.write_text(f"---\nid: {new}\n{raw[4:]}", encoding="utf-8")
    else:
        path.write_text(f"---\nid: {new}\n---\n{raw}", encoding="utf-8")
    return new, True


_LINK_LINE = re.compile(
    r"^(?P<head>\s*(?:-\s+)?(?:[A-Za-z0-9_.'\"-]+\s*:\s*)?)(?P<q>[\"']?)(?P<name>[^\s\"'#:]+)(?P=q)(?P<tail>\s*(?:#.*)?)$"
)


def move(old: str, new: str, root: Optional[Path] = None) -> List[Path]:
    """Rename prompt *old* to *new*: its file, its versions and labels, and
    every link to it in composites (``use``/``pick``/``when``), published
    versions included. Its id does not change.
    Returns the files rewritten."""
    base = prompts_dir(root)
    src = _path(base, old)
    if not src.is_file():
        raise PromptNotFound(f"no prompt {old!r} in {base}")
    _path(base, new)  # a name it can be
    if any((base / f"{new}{suffix}").exists() for suffix in _SUFFIXES):
        raise PromptFormatError(f"prompt {new!r} already exists")
    dst = base / f"{new}{src.suffix}"
    dst.parent.mkdir(parents=True, exist_ok=True)
    src.rename(dst)
    vold, vnew = base / ".versions" / old, base / ".versions" / new
    if vold.is_dir():
        vnew.parent.mkdir(parents=True, exist_ok=True)
        vold.rename(vnew)
    table = labels(base.parent)
    if old in table:
        table[new] = table.pop(old)
        _write_labels(base, table)
    changed: List[Path] = [dst]
    for path in sorted(base.rglob("*")):
        if not path.is_file() or path.suffix not in _SUFFIXES:
            continue
        text = path.read_text(encoding="utf-8")
        if path.suffix == ".md":
            continue
        else:
            lines = []
            for line in text.split("\n"):
                m = _LINK_LINE.match(line)
                head = m.group("head").strip() if m else ""
                key = head.lstrip("-").strip().rstrip(":").strip().strip("'\"")
                if (
                    m
                    and m.group("name") == old
                    and key
                    not in ("id", "kind", "description", "join", "pick", "when", "var", "text")
                ):
                    line = f"{m.group('head')}{m.group('q')}{new}{m.group('q')}{m.group('tail')}"
                lines.append(line)
            out = "\n".join(lines)
        if out != text:
            path.write_text(out, encoding="utf-8")
            if path not in changed:
                changed.append(path)
    return changed
