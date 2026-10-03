"""The operonx guide for coding assistants — Markdown shipped with the package.

Start at ``README.md`` in this directory (``operonx guide --path``, or
``python -m operonx.guide``, prints where it is). Every example in the guide
is run by operonx's test suite, so the guide matches the installed version.

A project keeps a copy in ``.operonx/guide/`` (``operonx guide --sync``), so
an assistant reads it beside the code instead of hunting for site-packages.
"""

from __future__ import annotations

from pathlib import Path
from typing import Union

__all__ = ["path", "pages", "sync", "COPY_DIR"]

#: Where a project keeps its copy of the guide, relative to its root.
COPY_DIR = Path(".operonx") / "guide"


def path() -> Path:
    """The guide's directory."""
    return Path(__file__).resolve().parent


def pages() -> list:
    """The guide's pages, in reading order (``README.md`` first)."""
    here = path()
    return [here / "README.md"] + sorted(p for p in here.glob("[0-9]*.md"))


def sync(project: Union[str, Path]) -> Path:
    """Copy the installed guide into ``<project>/.operonx/guide/``.

    The copy is the installed pages byte for byte, plus ``VERSION`` (the
    operonx version they describe). A page the installed version no longer
    has is removed, so the copy never mixes two versions. Returns the
    copy's directory.
    """
    from operonx import __version__

    dest = Path(project) / COPY_DIR
    dest.mkdir(parents=True, exist_ok=True)
    keep = {p.name for p in pages()}
    for old in dest.glob("*.md"):
        if old.name not in keep:
            old.unlink()
    for page in pages():
        (dest / page.name).write_bytes(page.read_bytes())
    (dest / "VERSION").write_text(f"{__version__}\n", encoding="utf-8")
    return dest
