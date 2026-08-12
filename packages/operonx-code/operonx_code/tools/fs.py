"""Filesystem tools — read, write, edit.

Every one resolves its path through the :class:`~operonx_code.workspace.Workspace`,
so containment and the read-before-edit ledger apply uniformly. A tool that
resolved paths itself would be the one that eventually forgot to.
"""

from __future__ import annotations

from operonx.agents import tool
from operonx_code.workspace import WorkspaceError, active_workspace

__all__ = ["read_file", "write_file", "edit_file"]

_READ_SCHEMA = {
    "type": "object",
    "properties": {
        "path": {
            "type": "string",
            "description": "File to read, relative to the workspace root.",
        },
        "offset": {
            "type": "integer",
            "description": "First line to return, 1-indexed. Omit to start at the top.",
        },
        "limit": {
            "type": "integer",
            "description": "How many lines to return. Omit for the whole file.",
        },
    },
    "required": ["path"],
}

_WRITE_SCHEMA = {
    "type": "object",
    "properties": {
        "path": {"type": "string", "description": "File to write, relative to the root."},
        "content": {"type": "string", "description": "Full new contents of the file."},
    },
    "required": ["path", "content"],
}

_EDIT_SCHEMA = {
    "type": "object",
    "properties": {
        "path": {"type": "string", "description": "File to modify."},
        "old": {
            "type": "string",
            "description": (
                "Exact text to replace, including indentation. Must appear "
                "exactly once unless replace_all is true."
            ),
        },
        "new": {"type": "string", "description": "Replacement text."},
        "replace_all": {
            "type": "boolean",
            "description": "Replace every occurrence instead of requiring exactly one.",
        },
    },
    "required": ["path", "old", "new"],
}


@tool(
    name="read",
    description=(
        "Read a text file from the workspace. Returns numbered lines. "
        "Read a file before editing it — edits are refused otherwise."
    ),
    schema=_READ_SCHEMA,
    readonly=True,
    max_result_chars=60_000,
)
async def read_file(path: str, offset: int = 0, limit: int = 0) -> dict:
    ws = active_workspace()
    target = ws.resolve(path)
    if target.is_dir():
        raise WorkspaceError(f"{ws.relative(target)} is a directory — use glob to list it")
    if not target.exists():
        raise WorkspaceError(f"{ws.relative(target)} does not exist")

    text, truncated = ws.read_text(target)
    lines = text.splitlines()
    start = max(int(offset or 1), 1) - 1
    end = start + int(limit) if limit else len(lines)
    window = lines[start:end]

    # Line numbers because every later reference — an edit, a review
    # comment, a traceback — is by line. Returning bare text makes the
    # model count, and it counts wrong.
    numbered = "\n".join(f"{start + i + 1}\t{line}" for i, line in enumerate(window))
    return {
        "path": ws.relative(target),
        "content": numbered,
        "lines": len(lines),
        "truncated": truncated or end < len(lines),
    }


@tool(
    name="write",
    description=(
        "Create a file, or replace one entirely. For a change to part of an "
        "existing file use edit — this discards everything not in `content`."
    ),
    schema=_WRITE_SCHEMA,
    destructive=True,
)
async def write_file(path: str, content: str) -> dict:
    ws = active_workspace()
    target = ws.resolve(path)
    existed = target.exists()
    if existed and not ws.has_read(target):
        # Overwriting unseen content is the same mistake as a blind edit,
        # and `write` is the tool that does it wholesale.
        raise WorkspaceError(
            f"{ws.relative(target)} already exists and has not been read. "
            f"Read it first, or pick a different path."
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    ws.record_read(target, content.encode("utf-8"))
    return {
        "path": ws.relative(target),
        "created": not existed,
        "bytes": len(content.encode("utf-8")),
    }


@tool(
    name="edit",
    description=(
        "Replace an exact string in a file. The file must have been read "
        "in this session and be unchanged since. `old` must match exactly, "
        "including indentation."
    ),
    schema=_EDIT_SCHEMA,
    destructive=True,
)
async def edit_file(path: str, old: str, new: str, replace_all: bool = False) -> dict:
    ws = active_workspace()
    target = ws.resolve(path)
    ws.check_fresh(target)

    text = target.read_text(encoding="utf-8")
    count = text.count(old)
    if count == 0:
        raise WorkspaceError(
            f"`old` does not appear in {ws.relative(target)}. It must match the "
            f"file exactly, including indentation and line breaks."
        )
    if count > 1 and not replace_all:
        # Replacing the first of several is a coin flip the caller did not
        # know they were tossing.
        raise WorkspaceError(
            f"`old` appears {count} times in {ws.relative(target)}. Include "
            f"more surrounding context to make it unique, or pass replace_all."
        )
    if old == new:
        raise WorkspaceError("`old` and `new` are identical — nothing to do")

    updated = text.replace(old, new) if replace_all else text.replace(old, new, 1)
    target.write_text(updated, encoding="utf-8")
    ws.record_read(target, updated.encode("utf-8"))
    return {
        "path": ws.relative(target),
        "replaced": count if replace_all else 1,
    }
