"""Moved: use :mod:`operonx.app.manifest`. Kept for one release."""

from operonx.core._moved import alias as _alias

_alias("operonx.core.manifest", "operonx.app.manifest")
from operonx.app.manifest import *  # noqa: E402,F401,F403
from operonx.app.manifest import (  # noqa: E402,F401 — names tests and tools reach for
    _ENTRY_RE,
    _interpolate_env_vars,
    _toml,
)
