"""The application layer moved from ``operonx.core`` to ``operonx.app``
(1.8.0). These shims keep the old import paths working for one minor
release, with a warning, so a project can move at its own pace."""

from __future__ import annotations

import importlib
import sys
import warnings


def alias(old: str, new: str, submodules: tuple = ()) -> None:
    """Register ``new`` (and its submodules) under ``old`` in ``sys.modules``."""
    warnings.warn(
        f"`{old}` moved to `{new}` in operonx 1.8.0; the old path will go in 1.9.0",
        DeprecationWarning,
        stacklevel=3,
    )
    target = importlib.import_module(new)
    sys.modules[old] = target
    for name in submodules:
        sys.modules[f"{old}.{name}"] = importlib.import_module(f"{new}.{name}")
