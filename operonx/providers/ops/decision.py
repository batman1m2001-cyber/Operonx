"""DecisionOp — typed questions about a state, answered with probabilities."""

from typing import Any, Dict, Optional

from operonx.core.configs import OpType
from operonx.core.ops import BaseOp
from operonx.core.ops.base import shorthand, split_shorthand_kwargs
from operonx.core.utils.common import Param
from operonx.providers.ops._runtime_resource import RESOURCE_PARAM, per_call, take_resource
from operonx.providers.ops._utils import resolve_hub


class DecisionOp(BaseOp):
    """Ask a ``decision:`` resource (TypeSafe Jev) typed questions about a state.

    Every question is answered in one request, in parallel, with
    probabilities — no text is generated. The questions are the System One
    wire format, name -> question (``noul``, ``choice``, ``score``, ``box``),
    so TypeSafe's docs apply as written.

    Inputs:
        state (str | dict | list): What the questions are about. A dict is
            first-class: structured state reads better than a template.
        questions (dict): name -> question. Required; usually a static
            value at ``.of(...)``.
        assets (dict): Images the state refers to, by placeholder. Optional.

    Outputs:
        answers (dict): name -> the server's answer, with ``confidence``
            added where it sent none (a noul: ``|2p - 1|``).
        usage (dict): ``prompt_tokens``, ``completion_tokens``,
            ``total_tokens`` — the run row counts them as a model's.
        cost_usd (float | None): Reported by the route, or from the
            resource's ``cost_per_input_token``; None when neither.
        model_used (str): The versioned model that answered.

    Example::

        triage = DecisionOp.of(
            resource="jev",
            state=PARENT["message"],
            questions={
                "refund": {"type": "noul", "instructions": "The customer asks for a refund."},
                "tone": {"type": "choice", "instructions": "The customer's tone.",
                         "criteria": {"calm": None, "angry": None, "confused": None}},
            },
        )
        # triage["answers"] -> {"refund": {"type": "noul", "noul": 0.91, "confidence": 0.82},
        #                       "tone": {"type": "choice", "choice": "angry", ...}}

    Turn an answer into a branch in an ``@op`` (``verdict(answer, at=0.7)``
    in :mod:`operonx.providers.decisions` applies TypeSafe's thresholds).
    """

    show_keys_default = ("answers",)

    __slots__ = ["resource", "backend", "_initialized", "_dynamic", "_bound"]

    type: OpType = "decision"

    def __init__(
        self,
        resource: Optional[str] = None,
        inputs: Dict[str, Any] = None,
        outputs: Dict[str, Any] = None,
        **kwargs: Any,
    ):
        kwargs.setdefault("bound", "io")
        super().__init__(**kwargs)

        resource, inputs, self._dynamic = take_resource(resource, inputs)
        self._bound = {}
        self.resource = resource

        input_schema = {
            "state": Param(type=(str, dict, list), required=True),
            "questions": Param(type=dict, required=True),
            "assets": Param(type=dict, default=None),
        }
        output_schema = {
            "answers": Param(type=dict, required=True),
            "usage": Param(type=dict, default={}),
            "cost_usd": Param(type=float, default=None),
            "model_used": Param(type=str, default=None),
        }
        if self._dynamic:
            input_schema = {**input_schema, "resource": RESOURCE_PARAM}
        self.inputs = self._merge_params(input_schema, inputs)
        self.outputs = self._merge_params(output_schema, outputs)

        self.backend = None
        self._initialized = False
        self._set_core(per_call(self, "_process") if self._dynamic else self._process)

    def warmup(self) -> None:
        """Resolve the backend on engine startup (a wrong key fails here)."""
        if self._dynamic:
            return
        self._ensure_initialized()

    def _ensure_initialized(self):
        if self._initialized:
            return
        self.backend = resolve_hub().get(f"decision:{self.resource}")
        if not getattr(self.backend, "name", None):
            self.backend.name = self.resource  # for its error messages
        self._initialized = True

    async def _process(
        self, state: Any, questions: Dict[str, Any], assets: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        self._ensure_initialized()
        # A DecisionError (a status, a wrong shape) and a transport error are
        # raised as they are: the second stays transient to an op's retry=.
        decision = await self.backend.decide(state, questions, assets)
        return {
            "answers": decision.answers,
            "usage": decision.usage,
            "cost_usd": decision.cost_usd,
            "model_used": decision.model or self.resource,
        }

    @shorthand
    def of(cls, resource=None, **kwargs) -> "DecisionOp":
        """Create a DecisionOp with flat kwargs.

        Example::

            DecisionOp.of(resource="jev", state=PARENT["msg"], questions={...})
        """
        input_mappings, init_kwargs = split_shorthand_kwargs(kwargs)
        return cls(resource=resource, inputs=input_mappings or None, **init_kwargs)

    def serialize(self) -> dict:
        """Serialize, with the resolved backend's config (its key left out)."""
        if not self._dynamic:
            self._ensure_initialized()
        base = super().serialize()
        base["resource"] = self.resource
        if self.backend is not None and hasattr(self.backend, "config"):
            base["resource_config"] = self.backend.config.model_dump(
                mode="json", exclude={"api_key"}
            )
        return base

    @property
    def specific_metadata(self) -> Dict[str, Any]:
        return {"model": self.resource}
