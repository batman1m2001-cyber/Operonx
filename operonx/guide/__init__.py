"""The operonx guide for coding assistants — Markdown shipped with the package.

Start at ``README.md`` in this directory (``python -m operonx.guide`` prints
where it is). Every example in the guide is run by operonx's test suite, so
the guide matches the installed version.
"""

from __future__ import annotations

from pathlib import Path

__all__ = ["path", "pages"]


def path() -> Path:
    """The guide's directory."""
    return Path(__file__).resolve().parent


def pages() -> list:
    """The guide's pages, in reading order (``README.md`` first)."""
    here = path()
    return [here / "README.md"] + sorted(p for p in here.glob("[0-9]*.md"))
