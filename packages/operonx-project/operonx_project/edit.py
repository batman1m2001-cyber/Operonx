"""Surgical edits to a project's config files.

Every function here takes the file's text and returns new text, changing
only the span that must change. Nothing is parsed and re-serialised.

That is not fastidiousness. Measured on the three real resource files in
this workspace, a parse-and-reserialise round-trip through PyYAML drops
57–76% of each file and every comment in it:

    ex16   resources.yaml   1735b -> 422b    (34 comment lines gone)
    operon resources.yaml   5668b -> 2432b   (68 comment lines gone)
    callbot resources.yaml  3555b -> 1098b   (45 comment lines gone)

Those comments are the file's documentation — the two-store explanation,
the HNSW opclass warning, why a block is commented out. A UI that silently
deleted them while "saving your resource change" would be worse than one
that could not edit at all.

Hence the contract every function upholds, and every test asserts:

    **An edit that changes nothing returns the input byte for byte.**

That makes the round-trip gate trivially true rather than aspirational, and
means a save that touches one value produces a one-line diff a reviewer can
read.
"""

from __future__ import annotations

import re
from typing import List, Optional

__all__ = [
    "set_env_var",
    "unset_env_var",
    "set_resource_field",
    "EditError",
]


class EditError(Exception):
    """The requested edit does not apply to this file."""


# The separator absorbs the whitespace around `=` so `NAME = value` keeps
# its spacing, and the captured value never carries a stray leading space
# that would then look like it needed quoting.
_ENV_ASSIGNMENT = re.compile(
    r"^(\s*)(?:(export)\s+)?([A-Za-z_][A-Za-z0-9_]*)(\s*=\s*)(.*)$"
)


def _split_env_comment(raw: str) -> tuple[str, str]:
    """Separate a value from a trailing ``# comment``.

    Real ``.env`` files carry them — callbot's has
    ``NEED_STT_EMB = "true"  # Whether to get embedding from STT model`` —
    and treating the comment as part of the value re-quotes the whole line
    into nonsense. A ``#`` inside quotes belongs to the value.
    """
    body = raw.strip()
    if body[:1] in ("\"", "'"):
        quote = body[0]
        end = body.find(quote, 1)
        while end != -1 and body[end - 1] == "\\":
            end = body.find(quote, end + 1)
        if end != -1:
            return body[: end + 1], raw[raw.index(body) + end + 1 :]
    marker = raw.find("#")
    if marker > 0 and raw[marker - 1].isspace():
        return raw[:marker].strip(), raw[marker:]
    return raw.strip(), ""


def _needs_quoting(value: str) -> bool:
    return value != value.strip() or any(c in value for c in " \t#\"'$")


def _render_env_value(value: str) -> str:
    if not _needs_quoting(value):
        return value
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def set_env_var(text: str, name: str, value: str) -> str:
    """Set ``name`` to ``value``, in place if it already exists.

    An existing assignment keeps its position, indentation and ``export``
    prefix, so a diff shows one changed line rather than a reordered file.
    A commented-out assignment is left alone and a live one appended — the
    comment is usually documentation of the default, and rewriting it would
    destroy the explanation while looking like a successful edit.

    **Every** live assignment is updated, not just the first. Duplicates do
    occur — callbot's ``.env_staging`` sets ``VAD_NEED_PADDING`` at line 51
    and again at line 94 — and since dotenv loaders take the last one,
    rewriting only the first would leave the effective value unchanged while
    showing the user a successful edit.
    """
    rendered_default = _render_env_value(value)
    lines = text.splitlines(keepends=True)
    changed = False
    found = False
    for i, line in enumerate(lines):
        rendered = rendered_default
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        match = _ENV_ASSIGNMENT.match(line.rstrip("\n"))
        if not match or match.group(3) != name:
            continue
        indent, export, key, eq, old = match.groups()
        old_body, trailing = _split_env_comment(old)
        # If the file quotes this value, keep quoting it. Dropping the quotes
        # is a real change to a shell-sourced file, not a formatting nicety.
        if len(old_body) >= 2 and old_body[0] == old_body[-1] and old_body[0] in "\"'":
            rendered = _render_env_value(value) if _needs_quoting(value) else f'"{value}"'
        ending = line[len(line.rstrip("\n")):]
        replacement = (
            f"{indent}{(export + ' ') if export else ''}{key}{eq}{rendered}{trailing}{ending}"
        )
        if replacement != line:
            lines[i] = replacement
            changed = True
        found = True

    if found:
        return "".join(lines) if changed else text

    prefix = "" if (not text or text.endswith("\n")) else "\n"
    return f"{text}{prefix}{name}={rendered_default}\n"


def unset_env_var(text: str, name: str) -> str:
    """Remove every live assignment of ``name``. Absent name → unchanged."""
    lines = text.splitlines(keepends=True)
    kept: List[str] = []
    removed = False
    for line in lines:
        if not line.lstrip().startswith("#"):
            match = _ENV_ASSIGNMENT.match(line.rstrip("\n"))
            if match and match.group(3) == name:
                removed = True
                continue
        kept.append(line)
    return "".join(kept) if removed else text


def _yaml_scalar(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value)
    quoted = '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'
    if text == "" or text[0] in "&*!|>%@`{[\"'" or ": " in text or text.strip() != text:
        return quoted
    # A bare scalar must parse back to the *same string*. Values like
    # ``2025-04`` or ``true`` or ``0755`` do not — YAML reads them as a date,
    # a bool, an int — so emitting them unquoted silently changes the config.
    try:
        import yaml

        if yaml.safe_load(text) != text:
            return quoted
    except Exception:  # noqa: BLE001 - unparseable bare form; quote it
        return quoted
    return text


def set_resource_field(text: str, resource: str, field: str, value: object) -> str:
    """Set one scalar ``field`` under top-level ``resource``.

    Only the value is replaced; the key's indentation, any trailing comment
    and every surrounding line survive untouched. Commented-out blocks are
    skipped — ex16 has both a live ``doc_store:corpus`` and a commented one,
    and editing the dead copy would look like success and change nothing.

    Raises:
        EditError: the resource or the field is not present. Silently
            appending would create config the author never wrote.
    """
    lines = text.splitlines(keepends=True)
    start: Optional[int] = None
    for i, line in enumerate(lines):
        if line.lstrip().startswith("#"):
            continue
        if line.startswith(f"{resource}:") and not line[0].isspace():
            start = i
            break
    if start is None:
        raise EditError(f"resource {resource!r} not found")

    for i in range(start + 1, len(lines)):
        line = lines[i]
        stripped = line.strip()
        if stripped and not line[0].isspace():
            break  # next top-level key — field is absent
        if not stripped or stripped.startswith("#"):
            continue
        match = re.match(r"^(\s+)([A-Za-z0-9_.-]+)(\s*:\s*)(.*)$", line.rstrip("\n"))
        if not match or match.group(2) != field:
            continue
        indent, key, sep, rest = match.groups()
        # Preserve the author's representation. ``api_version: "2025-04"`` is
        # quoted on purpose; re-emitting it bare changes the file and risks
        # changing the type YAML infers.
        bare = rest.split("#")[0].strip() if "#" in rest else rest.strip()
        if len(bare) >= 2 and bare[0] == bare[-1] and bare[0] in "\"'":
            if bare[1:-1] == str(value):
                return text
        comment = ""
        if "#" in rest:
            body, _, tail = rest.partition("#")
            # Only a `#` that follows whitespace is a comment; one inside the
            # value (a URL fragment, say) belongs to the value. Keep the exact
            # run of spaces so column-aligned comments stay aligned and the
            # diff stays one changed token wide.
            if body.rstrip() != body or not body.strip():
                comment = body[len(body.rstrip()):] + "#" + tail
        ending = line[len(line.rstrip("\n")):]
        replacement = f"{indent}{key}{sep}{_yaml_scalar(value)}{comment}{ending}"
        if replacement == line:
            return text
        lines[i] = replacement
        return "".join(lines)

    raise EditError(f"resource {resource!r} has no field {field!r}")
