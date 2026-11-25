"""Factory for ``decision:`` backends.

Each backend is lazy-imported in its branch: the HTTP one needs httpx (the
``providers`` extra), the fake one needs nothing beyond it.
"""

from operonx.providers.decisions.base import BaseDecider
from operonx.providers.decisions.config import DecisionConfig, DecisionType


def create_decision(config: DecisionConfig, name: str = "") -> BaseDecider:
    """Build the backend ``config.api_type`` names.

    Raises:
        ValueError: An api_type with no backend, or a missing api_key.
        ImportError: httpx is not installed (``pip install operonx[providers]``).
    """
    if config.api_type in (DecisionType.TYPESAFE, DecisionType.OPENROUTER, DecisionType.RUN):
        try:
            from operonx.providers.decisions.system_one import RunDecider, SystemOneDecider
        except ImportError as e:
            raise ImportError(
                "a decision: resource needs httpx.\n"
                "  Install with: pip install operonx[providers]\n"
                f"  Original error: {e}"
            ) from e
        if config.api_type == DecisionType.RUN:
            return RunDecider(config, name)
        return SystemOneDecider(config, name)
    if config.api_type == DecisionType.FAKE:
        from operonx.providers.decisions.fake import FakeDecider

        return FakeDecider(config, name)
    raise ValueError(f"Unsupported decision api_type: {config.api_type}")
