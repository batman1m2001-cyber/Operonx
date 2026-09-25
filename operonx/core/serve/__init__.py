"""Moved: use :mod:`operonx.app.serve`. Kept for one release."""

from operonx.core._moved import alias as _alias

_alias(
    "operonx.core.serve",
    "operonx.app.serve",
    ("app", "asgi", "memory", "ops", "protocol", "registry", "runner"),
)
from operonx.app.serve import *  # noqa: E402,F401,F403
from operonx.app.serve import __all__  # noqa: E402,F401
