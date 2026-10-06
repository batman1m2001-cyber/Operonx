"""Shared fixtures. ``hub(...)`` installs scripted backends as the
ResourceHub for one test."""

from __future__ import annotations

import pytest
from operonx.core.registry.resource_hub import ResourceHub

from tests.fakes import FakeHub


@pytest.fixture
def hub():
    installed = []

    def install(**llms):
        fake = FakeHub(**llms)
        ResourceHub.set_instance(fake)
        installed.append(fake)
        return fake

    yield install
    ResourceHub.reset_instance()
