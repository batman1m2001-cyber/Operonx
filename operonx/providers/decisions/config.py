"""``decision:`` resources: a typed classifier spoken over the System One API.

    decision:jev:
      api_type: openrouter          # typesafe | openrouter | run | fake
      api_key: ${OPENROUTER_API_KEY}
      model: typesafe/jev-1.14      # required: answers differ between versions

A Jev model served on your own network takes the same questions:

    decision:glide:
      api_type: run                 # the self-hosted endpoint's envelope
      base_url: https://jev.internal/api/run
      model: glide-2b

    decision:sl:
      api_type: typesafe            # the System One body, at another URL
      base_url: http://10.0.0.7/v1
      path: /decisions
      model: sl-4b

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
    RUN = "run"  # a self-hosted Jev endpoint: the request wrapped in an envelope
    FAKE = "fake"  # scripted answers, no network


#: The ``base_url`` of each api_type, the part before ``/systemone``.
DEFAULT_BASE_URLS = {
    DecisionType.TYPESAFE: "https://api.typesafe.ai/v1",
    DecisionType.OPENROUTER: "https://openrouter.ai/api/v1",
}

#: What follows ``base_url`` when the resource sets no ``path``.
DEFAULT_PATHS = {
    DecisionType.TYPESAFE: "/systemone",
    DecisionType.OPENROUTER: "/systemone",
    DecisionType.RUN: "",
}

#: The call retry when the resource sets none: TypeSafe's own SDK retries a
#: 429 or 529, and its docs ask clients to. A call takes ~0.7 s.
RETRY_DEFAULTS = {"attempts": 3, "initial": 1.0, "max_interval": 30.0}


class DecisionConfig(YamlModel):
    """A ``decision:`` resource.

    Attributes:
        api_type: ``typesafe``, ``openrouter``, ``run`` or ``fake``.
            ``run`` is a self-hosted Jev endpoint: POST ``base_url`` with
            ``{model, request: {state, questions}}``, answered in an
            envelope ``{status, problem, response: {answers, usage,
            model}}``.
        api_key: Bearer key of the route (``${TYPESAFE_API_KEY}``,
            ``${OPENROUTER_API_KEY}``). Required on TypeSafe's and
            OpenRouter's own hosts; a self-hosted endpoint (another
            ``base_url``, or ``run``) may take none, and is then sent no
            ``Authorization`` header.
        base_url: Up to ``/v1``; ``path`` is appended. Defaults to the
            api_type's; ``run`` has none, and needs one.
        path: Appended to ``base_url``: ``/systemone`` on ``typesafe`` and
            ``openrouter``, nothing on ``run`` (its ``base_url`` is the
            whole URL). For a System One body served at another path
            (``/decisions``).
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
    path: Optional[str] = None
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
        if self.api_type == DecisionType.RUN and not self.base_url:
            raise ValueError(
                "a decision: resource with api_type: run needs base_url: (the endpoint's whole "
                "URL, e.g. https://jev.internal/api/run). A self-hosted endpoint has no default."
            )
        if self.base_url is None:
            self.base_url = DEFAULT_BASE_URLS.get(self.api_type)
        if self.path is None:
            self.path = DEFAULT_PATHS.get(self.api_type, "")
        return self

    @property
    def url(self) -> str:
        """Where a request goes: ``base_url`` then ``path``."""
        path = self.path or ""
        if path and not path.startswith("/"):
            path = "/" + path
        return f"{(self.base_url or '').rstrip('/')}{path}"

    @property
    def needs_key(self) -> bool:
        """An api_key is required on TypeSafe's and OpenRouter's own hosts only."""
        default = DEFAULT_BASE_URLS.get(self.api_type)
        return default is not None and (self.base_url or "").rstrip("/") == default

    @classmethod
    def default(cls) -> "DecisionConfig":
        """A fake resource: what a template or a test starts from."""
        return cls(api_type=DecisionType.FAKE)
