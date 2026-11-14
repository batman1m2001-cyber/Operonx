"""Helpers for a project's tests.

``fake_model`` scripts one ``llm:`` resource for the length of a ``with``
block: a test that needs the model to fail, stall or answer something in
particular says so where it runs, whatever ``conftest.py`` installed::

    from operonx.testing import fake_model

    def test_one_503_is_retried():
        with fake_model({"status": 503}, "a summary", retry={"max_attempts": 3, "initial": 0.01}) as model:
            ...  # run the job or call the route
        assert len(model.calls) == 2  # the messages of every call

A turn is what ``script:`` takes in ``resources.yaml`` (see
:mod:`operonx.providers.llms.fake`): a string, ``{"status": 503}``,
``{"delay": 0.5, "text": ...}``, ``{"tool_calls": [...]}``,
``{"echo": "Echo: "}``. The last turn repeats. Other keywords are the
resource's own (``retry=``, ``chunk_size=``).
"""

from __future__ import annotations

import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

__all__ = ["fake_model"]


@contextmanager
def fake_model(*script: Any, key: str = "llm:assistant", **config: Any) -> Iterator[Any]:
    """Answer *key* from *script* inside the block, then put back what it was.

    It swaps the resource in the installed hub (one is installed for the
    block when there is none), in this process only: ``resources.yaml`` is
    not touched. Ops that already ran (an engine warmed up by a session
    fixture) take the fake inside the block and what was there after it.
    Yields the fake model; its ``calls`` hold the messages of every call it
    answered.
    """
    from operonx.core.registry.resource_hub import ResourceHub
    from operonx.providers.llms.config import FakeConfig

    key = key if key.startswith("llm:") else f"llm:{key}"
    installed = ResourceHub._instance is None
    if installed:
        empty = Path(tempfile.mkdtemp(prefix="operonx-fake-")) / "resources.yaml"
        empty.write_text("{}\n")
        ResourceHub.set_instance(ResourceHub.from_yaml(empty))
    hub = ResourceHub._instance
    before = hub._cache.get(key)
    fake = FakeConfig.model_validate({"api_type": "fake", "script": list(script) or None, **config})
    hub.register(fake, key, persist=False)
    try:
        yield hub.get(key)
    finally:
        if installed:
            ResourceHub.set_instance(None)
        elif before is not None:
            hub._cache[key] = before
        else:
            hub._cache.pop(key, None)
        ResourceHub.revision += 1
