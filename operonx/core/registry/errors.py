"""Warnings, errors, and shared bootstrap state for ResourceHub setup.

This module is the single home for cross-cutting setup concerns so that
``storage/yaml.py`` and ``resource_hub.py`` can share types without a
circular import.
"""

from pathlib import Path
from typing import List

# Populated by ``operonx.bootstrap()`` with the absolute paths of every
# ``.env`` file that was loaded (or attempted). Used by ``EnvVarUnsetError``
# so the user knows where ``.env`` was searched when a ``${VAR}`` lookup fails.
BOOTSTRAP_ENV_PATHS: List[Path] = []


class ResourceHubWarning(UserWarning):
    """Warning emitted by ResourceHub setup (``auto`` / ``from_yaml`` / ``bootstrap``).

    Subclassing ``UserWarning`` lets users silence ResourceHub-specific
    warnings without muting unrelated ``UserWarning`` traffic::

        import warnings
        from operonx.core.registry import ResourceHubWarning
        warnings.filterwarnings("ignore", category=ResourceHubWarning)
    """


class EnvVarUnsetError(RuntimeError):
    """Raised when a required ``${VAR}`` reference cannot be resolved.

    Subclasses ``RuntimeError`` so existing ``except RuntimeError`` callers
    continue to work — this preserves backwards-compatible behavior with
    the previous ``_raise_missing_env_vars`` helper.
    """


class ResourceUnreachable(RuntimeError):
    """A resource's endpoint did not accept a connection.

    Distinct from a missing key or a bad config: the resource is declared
    correctly and its client constructs fine — nothing answered at the
    address. On a corporate setup that usually means the VPN is down, and
    the symptom without this check is every call timing out one by one
    instead of one error at the start.
    """

    def __init__(self, failures: dict):
        self.failures = dict(failures)
        lines = "\n".join(f"  {k}: {v}" for k, v in sorted(self.failures.items()))
        super().__init__(
            f"{len(self.failures)} resource(s) unreachable:\n{lines}\n"
            "Check the VPN, or pass only the keys you need to "
            "hub.require_reachable(...)."
        )
