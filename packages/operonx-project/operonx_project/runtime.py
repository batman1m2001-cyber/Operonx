"""What a run works with, outside the graph's inputs, and the model each role runs on.

``brd_flow`` is journalled, so its inputs are plain JSON: a runtime key and the source. The ops look the
rest up here (the coder, the folders). A process that resumes a run registers a runtime under the same key
first, and the run goes on with that process's objects.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from operonx.core.loggings import LOGGER

__all__ = ["DEFAULT_MODELS", "get_runtime", "register", "role_model", "unregister"]

#: The model each role's session runs on, unless ``settings.toml`` says otherwise.
DEFAULT_MODELS: Dict[str, Optional[str]] = {
    "surveyor": "haiku",  # adopt: the project's BRD from its docs and tests
    "extractor": "sonnet",  # brd_flow: the requirements, from a source
    "hunter": "fable",
}


def role_model(state: Path, role: str, fallback: Optional[str], risk: Optional[str] = None) -> Optional[str]:
    """The model *role*'s session runs on: ``settings.toml``'s ``[models]`` for ``<role>:<risk>``, then for
    that role, else its ``model`` (every role), else *fallback*. A file that does not parse is ignored, with a
    warning."""
    from operonx_project.project import load_settings

    try:
        got = load_settings(state)
    except ValueError:
        LOGGER.warning(f"operonx-project: {Path(state) / 'settings.toml'} does not parse; its models are ignored")
        return fallback
    per = got.get("models") or {}
    return (risk and per.get(f"{role}:{risk}")) or per.get(role) or got.get("model") or fallback


_REGISTRY: Dict[str, Any] = {}


def register(rt: Any) -> Any:
    _REGISTRY[rt.key] = rt
    return rt


def unregister(key: str) -> None:
    _REGISTRY.pop(key, None)


def get_runtime(key: str) -> Any:
    try:
        return _REGISTRY[key]
    except KeyError:
        raise KeyError(
            f"no runtime {key!r} in this process: register one (operonx_project.runtime.register) "
            "before running or resuming a run"
        ) from None
