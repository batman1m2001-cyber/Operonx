"""The scripted models live in :mod:`operonx_agents.testing` (public: the
users' tests need them too); this module re-exports them for the suite and
keeps what only the suite uses."""

from __future__ import annotations

from operonx_agents.testing import (  # noqa: F401 — re-exported
    FakeHub,
    ScriptedLLM,
    chunk,
    chunks_of,
    completion,
)


class StatusError(Exception):
    """An HTTP error with a status code, as the SDK raises them."""

    def __init__(self, status_code: int) -> None:
        super().__init__(f"HTTP {status_code}")
        self.status_code = status_code
