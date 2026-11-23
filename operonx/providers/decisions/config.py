"""``decision:`` resources: a typed classifier spoken over the System One API.

    decision:jev:
      api_type: openrouter          # typesafe | openrouter | fake
      api_key: ${OPENROUTER_API_KEY}
      model: typesafe/jev-1.14      # required: answers differ between versions

See docs/JEV_PLAN.md for the wire format these fields describe.
"""

from enum import Enum
from typing import Any, ClassVar, Dict, List, Optional

from pydantic import model_validator

from operonx.core.utils import YamlModel


class DecisionType(Enum):
    """Where the decisions are asked. ``typesafe`` and ``openrouter`` speak
    the same request; they differ in their default ``base_url`` and in how
    the model is named (``jev-1.14.0`` and ``typesafe/jev-1.14``)."""

    TYPESAFE = "typesafe"
    OPENROUTER = "openrouter"
    FAKE = "fake"  # scripted answers, no network


#: The ``base_url`` of each api_type, the part before ``/systemone``.
DEFAULT_BASE_URLS = {
    DecisionType.TYPESAFE: "https://api.typesafe.ai/v1",
    DecisionType.OPENROUTER: "https://openrouter.ai/api/v1",
}

#: The call retry when the resource sets none: TypeSafe's own SDK retries a
#: 429 or 529, and its docs ask clients to. A call takes ~0.7 s.
RETRY_DEFAULTS = {"attempts": 3, "initial": 1.0, "max_interval": 30.0}


class DecisionConfig(YamlModel):
    """A ``decision:`` resource.

    Attributes:
        api_type: ``typesafe``, ``openrouter`` or ``fake``.
        api_key: Bearer key of the route (``${TYPESAFE_API_KEY}``,
            ``${OPENROUTER_API_KEY}``).
        base_url: Up to ``/v1``; ``/systemone`` is appended. Defaults to
            the api_type's.
        model: The model id, pinned: ``jev-1.14.0`` (TypeSafe) or
            ``typesafe/jev-1.14`` (OpenRouter, which has no aliases). No
            default — the version changes the answers.
        timeout: Seconds for any one network wait of a request.
        long_context: Send ``TypeSafe-Danger-Zone-Long-Context: true``,
            which raises the context from 64k to 262k tokens on TypeSafe.
            OpenRouter took 173k tokens without it (docs/JEV_PLAN.md).
        cost_per_input_token: USD per input token, for a route that does
            not report a cost (TypeSafe direct: ``4.2e-8``). OpenRouter
            reports one, and it wins. Output is free.
        retry: The call retry (``{max_attempts: 3, initial: 1,
            max_interval: 30}``, the default). 429, 5xx and 529 only.
        headers: Extra request headers (OpenRouter's ``X-Title``).
        split_on_overflow: A request over the context with several
            questions is sent again as two halves.
        script: ``fake`` only — the answers, one entry per call, the last
            repeating. An entry maps a question name to its answer, or is
            ``{status: 429}``.
    """

    _category: ClassVar[str] = "decision"

    api_type: DecisionType = DecisionType.OPENROUTER
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    model: Optional[str] = None
    timeout: float = 30.0
    long_context: bool = False
    cost_per_input_token: Optional[float] = None
    retry: Optional[Dict[str, Any]] = None
    headers: Optional[Dict[str, str]] = None
    split_on_overflow: bool = True
    script: Optional[List[Any]] = None

    @model_validator(mode="after")
    def _complete(self) -> "DecisionConfig":
        from operonx.core.policy import check_resource_retry

        check_resource_retry(self, **RETRY_DEFAULTS)
        if self.api_type != DecisionType.FAKE and not self.model:
            raise ValueError(
                "a decision: resource needs model: (jev-1.14.0 on typesafe, typesafe/jev-1.14 "
                "on openrouter). There is no default: the version changes the answers."
            )
        if self.base_url is None:
            self.base_url = DEFAULT_BASE_URLS.get(self.api_type)
        return self

    @classmethod
    def default(cls) -> "DecisionConfig":
        """A fake resource: what a template or a test starts from."""
        return cls(api_type=DecisionType.FAKE)
