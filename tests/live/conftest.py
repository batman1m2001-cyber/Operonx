"""Live-endpoint tests — opt-in, network-bound, off by default.

Everything under ``tests/live/`` talks to a real service, using this
repo's own ``.env`` and ``resources.yaml``. Enable with::

    OPERONX_LIVE=1 pytest tests/live -v

Without that variable the whole directory skips, so a plain
``pytest tests/`` stays offline and CI-safe.

A test also skips when the resource it needs is missing from
``resources.yaml`` **or** when that block references an environment
variable ``.env`` does not set. So a partial ``.env`` runs the part it
can and names what it could not — adding credentials later turns the
skipped tests on with no code change.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

#: Set to enable this directory.
LIVE_ENV = "OPERONX_LIVE"

#: Overrides which env file is loaded, relative to the repo root.
ENV_FILE_ENV = "OPERONX_LIVE_ENV"

REPO_ROOT = Path(__file__).resolve().parents[2]


def _enabled() -> bool:
    return os.environ.get(LIVE_ENV, "").strip().lower() in ("1", "true", "yes", "on")


def pytest_collection_modifyitems(config, items):
    """Skip the whole directory unless explicitly enabled."""
    if _enabled():
        return
    skip = pytest.mark.skip(reason=f"live tests are opt-in — set {LIVE_ENV}=1")
    here = Path(__file__).parent
    for item in items:
        if here in Path(str(item.fspath)).parents:
            item.add_marker(skip)


@pytest.fixture(scope="session")
def live_hub():
    """A ResourceHub over this repo's ``resources.yaml``.

    Loads ``.env`` first, because the resource blocks are full of
    ``${VAR}`` that resolve from it.
    """
    if not _enabled():
        pytest.skip(f"{LIVE_ENV} not set")

    from dotenv import load_dotenv

    from operonx.core.registry import ResourceHub

    env_file = REPO_ROOT / (os.environ.get(ENV_FILE_ENV, "").strip() or ".env")
    if env_file.exists():
        load_dotenv(env_file, override=False)
    else:
        print(f"[live] no {env_file.name} — relying on the ambient environment")

    resources = REPO_ROOT / "resources.yaml"
    if not resources.exists():
        pytest.skip(f"no resources.yaml at {resources}")

    import operonx.providers  # noqa: F401 — registers llm/embedding/auth categories

    print(f"[live] resources={resources.name}  env={env_file.name}")
    return ResourceHub.from_yaml(resources)


@pytest.fixture(scope="session")
def require_env():
    """Skip unless every named environment variable is set and non-empty.

    Resource blocks for private endpoints declare `${VAR:}` — an *empty*
    default — because `hub.keys()` loads every block and a bare `${VAR}`
    would make simply listing resources raise for anyone without that
    tenant. The cost of that default is that an unconfigured resource
    resolves to a useless config instead of failing, so the skip decision
    moves here, where it can name the variable that is missing.
    """
    import os as _os

    def _require(*names: str):
        missing = [n for n in names if not _os.environ.get(n, "").strip()]
        if missing:
            pytest.skip(f"unset: {', '.join(missing)}")

    return _require


@pytest.fixture(scope="session")
def require_key(live_hub):
    """Resolve a resource, or skip with the reason it could not be.

    Two distinct misses, both a skip rather than a failure:

    * the key is not in ``resources.yaml`` at all;
    * it is, but a ``${VAR}`` it needs is unset, which the hub reports by
      raising :class:`EnvVarUnsetError` from the lookup itself.

    Naming which one happened is the point — a test that quietly skipped
    for the wrong reason is worse than one that failed.
    """

    def _require(key: str):
        try:
            if not live_hub.has(key):
                pytest.skip(f"{key!r} is not defined in resources.yaml")
        except Exception as e:  # EnvVarUnsetError and anything else
            pytest.skip(f"{key!r} is unresolvable: {type(e).__name__}: {e}")
        try:
            return live_hub.get(key)
        except Exception as e:
            pytest.skip(f"{key!r} failed to initialise: {type(e).__name__}: {e}")

    return _require
