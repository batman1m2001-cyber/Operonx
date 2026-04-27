"""Warnings, errors, and shared bootstrap state for ResourceHub setup.

This module is the single home for cross-cutting setup concerns so that
``storage/yaml.py`` and ``resource_hub.py`` can share types without a
circular import.
"""

from pathlib import Path
from typing import List

# Populated by ``operon.bootstrap()`` with the absolute paths of every
# ``.env`` file that was loaded (or attempted). Used by ``EnvVarUnsetError``
# so the user knows where ``.env`` was searched when a ``${VAR}`` lookup fails.
BOOTSTRAP_ENV_PATHS: List[Path] = []


class ResourceHubWarning(UserWarning):
    """Warning emitted by ResourceHub setup (``auto`` / ``from_yaml`` / ``bootstrap``).

    Subclassing ``UserWarning`` lets users silence ResourceHub-specific
    warnings without muting unrelated ``UserWarning`` traffic::

        import warnings
        from operon.core.registry import ResourceHubWarning
        warnings.filterwarnings("ignore", category=ResourceHubWarning)
    """


class EnvVarUnsetError(RuntimeError):
    """Raised when a required ``${VAR}`` reference cannot be resolved.

    Subclasses ``RuntimeError`` so existing ``except RuntimeError`` callers
    continue to work — this preserves backwards-compatible behavior with
    the previous ``_raise_missing_env_vars`` helper.
    """
