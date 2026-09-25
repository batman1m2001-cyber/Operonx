"""Moved: use :mod:`operonx.app.jobs`. Kept for one release."""

from operonx.core._moved import alias as _alias

_alias(
    "operonx.core.jobs",
    "operonx.app.jobs",
    ("_keys", "job", "record", "runbook", "runner", "session", "sinks", "sources"),
)
from operonx.app.jobs import *  # noqa: E402,F401,F403
from operonx.app.jobs import __all__  # noqa: E402,F401
