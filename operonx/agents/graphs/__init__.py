"""Agent subgraphs — composed from ops, never from a god-class.

Each module here exports a *factory* returning a ``@graph``, rather than
a module-level graph instance. A graph is built once and captures its
configuration at build time, so a shared instance would silently pin the
first caller's settings for every later one.
"""

from __future__ import annotations

from operonx.agents.graphs.dispatch import build_dispatch

__all__ = ["build_dispatch"]
