"""Tool registration.

``@tool`` registers at **import** time into a process-wide registry. That
makes rebuilding a session awkward: ``clear_registry()`` empties the
registry, and re-importing does nothing because the module is already in
``sys.modules``. :func:`register_all` re-adds the same factories, so an
agent can be rebuilt in one process without ``importlib.reload``.
"""

from __future__ import annotations

from operonx.agents import TOOL_REGISTRY
from operonx_code.tools.fs import edit_file, read_file, write_file
from operonx_code.tools.search import glob_files, grep_files
from operonx_code.tools.shell_tool import run_bash
from operonx_code.tools.web import fetch_url

#: Every tool this harness ships, in the order the model sees them.
#: Read-only ones first — the ordering is a weak prior, but a free one,
#: and inspecting before acting is the behaviour worth nudging.
ALL_TOOLS = [
    read_file,
    glob_files,
    grep_files,
    edit_file,
    write_file,
    run_bash,
    fetch_url,
]


def register_all() -> None:
    """Put every tool back in the registry, whatever state it is in.

    Idempotent, and safe after ``clear_registry()``. Registering the same
    factory under the same name is not the shadowing the duplicate check
    guards against — that is two *different* functions claiming one name.
    """
    for factory in ALL_TOOLS:
        TOOL_REGISTRY[factory._tool_meta["name"]] = factory


__all__ = [
    "ALL_TOOLS",
    "register_all",
    "read_file",
    "write_file",
    "edit_file",
    "glob_files",
    "grep_files",
    "run_bash",
    "fetch_url",
]
