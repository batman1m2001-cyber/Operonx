"""``llm_step`` — a typed, deadline-bounded LLM call as a graph op.

The workflow front door: the graph owns control flow, the step owns one
model call and its answer::

    classify = llm_step(
        model=Model("inhouse", deadline=0.9, settings=ModelSettings(logprobs=True)),
        system="{analyzer_system_prompt}",
        user="{intent_prompt}",
        output=Choice(from_input="allowed_intents", field="intent"),
        on_timeout="fallback",
        on_invalid="fallback",
    )

    @graph
    def turn(...):
        c = classify(analyzer_system_prompt=..., intent_prompt=..., allowed_intents=...)
        # c["value"], c["confidence"], c["outcome"], c["error"], c["usage"], ...

``system`` / ``user`` are templates: ``{name}`` is filled from the input of
that name (every other input, ``Choice(from_input=)``'s included, is also
passed). Or pass ``messages=`` at call time for a ready conversation.

Outputs:
    value: The answer (the label, the model instance, the scalar, the
        text), or the declared degrade value.
    confidence: For a ``Choice``, P(label) from logprobs (``None`` when
        the gateway returns none, ``0.0`` on a degrade).
    outcome: ``ok``, ``timeout``, ``invalid`` or ``error``.
    error: Why it degraded, else ``None``.
    usage: Tokens and cost of every request it made (a dict).
    latency_ms: Wall time of the step.
    model_used: The resource that answered (``None`` on a degrade).

``on_timeout`` / ``on_invalid`` / ``on_error`` turn a deadline, an answer
that never validated, or a model that failed into a value instead of an
exception: a voice call cannot wait on a hung gateway. A bare value is the
degrade ``value``; a dict sets outputs by name. ``None`` (the default)
raises, and the op's failure lands in ``$errors`` like any op's.

It has no side effects, and its outputs are written only when it
finishes, so a superseded step that is cancelled writes nothing.
"""

from __future__ import annotations

import string
import time
from typing import Any, Dict, List, Mapping, Optional, Set

from operonx.core.ops import BaseOp
from operonx.core.ops.base import split_shorthand_kwargs
from operonx.core.utils.auto_name import register_skip
from operonx.core.utils.common import Param

from operonx_agents.errors import ModelError, ModelTimeout, OutputInvalid
from operonx_agents.model.model import Model, ModelSettings
from operonx_agents.model.output import ask, shape_for
from operonx_agents.model.usage import Usage

__all__ = ["LLMStepOp", "llm_step"]

_OUTPUTS = ("value", "confidence", "outcome", "error", "usage", "latency_ms", "model_used")


def llm_step(
    *,
    model: Model,
    system: Optional[str] = None,
    user: Optional[str] = None,
    output: Any = str,
    on_timeout: Any = None,
    on_invalid: Any = None,
    on_error: Any = None,
    output_retries: int = 1,
    strategy: Optional[str] = None,
    settings: Optional[ModelSettings] = None,
):
    """An op factory: call it inside a ``@graph`` with the step's inputs.

    Args:
        model: The :class:`Model` (its deadline covers re-asks too).
        system: The system message template, or ``None`` for none.
        user: The user message template. Without it, give ``messages=``
            when calling the step.
        output: ``str`` (default), a :class:`Choice`, a pydantic model, or
            a type pydantic validates.
        on_timeout: The value when the deadline passes.
        on_invalid: The value when the answer never validates.
        on_error: The value when every resource fails or refuses.
        output_retries: Re-asks of an invalid answer.
        strategy: Override the resource's ``structured_output``.
        settings: Per-step request knobs over the model's.

    Raises:
        ValueError: for a degrade dict naming an output the step does not
            have — it would be dropped without a word.
    """
    for label, degrade in (
        ("on_timeout", on_timeout),
        ("on_invalid", on_invalid),
        ("on_error", on_error),
    ):
        if isinstance(degrade, dict):
            unknown = sorted(set(degrade) - set(_OUTPUTS))
            if unknown:
                raise ValueError(
                    f"llm_step({label}=...) names {unknown}, which are not outputs of the step "
                    f"({list(_OUTPUTS)}). A bare value sets 'value'."
                )
    config = dict(
        model=model,
        system=system,
        user=user,
        output=output,
        on_timeout=on_timeout,
        on_invalid=on_invalid,
        on_error=on_error,
        output_retries=output_retries,
        strategy=strategy,
        settings=settings,
    )

    def step(**kwargs: Any) -> LLMStepOp:
        inputs, init_kwargs = split_shorthand_kwargs(kwargs)
        return LLMStepOp(**config, inputs=inputs or None, **init_kwargs)

    register_skip(step)  # the op is named after the variable it is assigned to
    step.config = config  # type: ignore[attr-defined]
    return step


def _template_names(*templates: Optional[str]) -> Set[str]:
    names: Set[str] = set()
    for template in templates:
        if template:
            names.update(n for _, n, _, _ in string.Formatter().parse(template) if n)
    return names


class LLMStepOp(BaseOp):
    """The op :func:`llm_step` builds. Use the factory; see the module docs."""

    show_keys_default = ("value", "outcome")

    __slots__ = [
        "model",
        "system",
        "user",
        "output",
        "on_timeout",
        "on_invalid",
        "on_error",
        "output_retries",
        "strategy",
        "settings",
    ]

    type = "llm"

    def __init__(
        self,
        *,
        model: Model,
        system: Optional[str],
        user: Optional[str],
        output: Any,
        on_timeout: Any,
        on_invalid: Any,
        on_error: Any,
        output_retries: int,
        strategy: Optional[str],
        settings: Optional[ModelSettings],
        inputs: Optional[Dict[str, Any]] = None,
        outputs: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> None:
        kwargs.setdefault("bound", "io")
        super().__init__(**kwargs)
        self.model = model
        self.system = system
        self.user = user
        self.output = output
        self.on_timeout = on_timeout
        self.on_invalid = on_invalid
        self.on_error = on_error
        self.output_retries = output_retries
        self.strategy = strategy
        self.settings = settings

        input_schema: Dict[str, Param] = {"messages": Param(type=list, default=None)}
        for name in _template_names(system, user):
            input_schema[name] = Param(required=False, default=None)
        source = getattr(output, "from_input", None)
        if source:
            input_schema[source] = Param(type=list, required=False, default=None)
        normalized = self._normalize_params(inputs)
        for name in normalized:
            input_schema.setdefault(name, Param(required=False, default=None))
        output_schema = {
            "value": Param(default=None),
            "confidence": Param(type=float, default=None),
            "outcome": Param(type=str, required=True),
            "error": Param(type=str, default=None),
            "usage": Param(type=dict, default={}),
            "latency_ms": Param(type=float, required=True),
            "model_used": Param(type=str, default=None),
        }
        self.inputs = self._merge_params(input_schema, normalized)
        self.outputs = self._merge_params(output_schema, self._normalize_params(outputs))
        self._set_core(self._run)

    def warmup(self) -> None:
        """Resolve the model's resources at engine start, so a missing one
        fails the build rather than the first call."""
        for resource in self.model.resources:
            self.model.llm(resource)

    def _messages(self, inputs: Mapping[str, Any]) -> List[Dict[str, Any]]:
        given = inputs.get("messages")
        if given is not None:
            if self.user is not None:
                raise ValueError(
                    f"step {self.name!r}: messages= was given and the step has a user= "
                    "template; one of the two is the conversation. Drop one."
                )
            out = list(given)
            if self.system is not None:
                out.insert(0, {"role": "system", "content": _fill(self.system, inputs, self.name)})
            return out
        if self.user is None:
            raise ValueError(
                f"step {self.name!r} has no user= template and was given no messages=; "
                "there is nothing to send."
            )
        out = []
        if self.system is not None:
            out.append({"role": "system", "content": _fill(self.system, inputs, self.name)})
        out.append({"role": "user", "content": _fill(self.user, inputs, self.name)})
        return out

    async def _run(self, **inputs: Any) -> Dict[str, Any]:
        start = time.perf_counter()
        shape = shape_for(self.output, inputs)
        messages = self._messages(inputs)
        try:
            result = await ask(
                self.model,
                messages,
                shape,
                strategy=self.strategy,
                output_retries=self.output_retries,
                settings=self.settings,
            )
        except ModelTimeout as exc:
            return self._degrade(self.on_timeout, "timeout", exc, start)
        except OutputInvalid as exc:
            return self._degrade(self.on_invalid, "invalid", exc, start)
        except ModelError as exc:
            return self._degrade(self.on_error, "error", exc, start)
        return {
            "value": result.value,
            "confidence": result.confidence,
            "outcome": "ok",
            "error": None,
            "usage": result.usage.to_dict(),
            "latency_ms": (time.perf_counter() - start) * 1000.0,
            "model_used": result.response.model_used,
        }

    def _degrade(self, declared: Any, outcome: str, exc: Exception, start: float) -> Dict[str, Any]:
        if declared is None:
            raise exc
        out: Dict[str, Any] = {
            "value": None,
            "confidence": 0.0,
            "outcome": outcome,
            "error": str(exc),
            "usage": Usage().to_dict(),
            "latency_ms": (time.perf_counter() - start) * 1000.0,
            "model_used": None,
        }
        if isinstance(declared, dict):
            out.update(declared)
        else:
            out["value"] = declared
        return out

    @property
    def specific_metadata(self) -> Dict[str, Any]:
        return {"model": self.model.resource, "fallback": list(self.model.fallback)}


def _fill(template: str, inputs: Mapping[str, Any], step: str) -> str:
    missing = sorted(n for n in _template_names(template) if inputs.get(n) is None)
    if missing:
        raise ValueError(
            f"step {step!r}: the template needs {missing}, which arrived empty. Pass them "
            f"as inputs: {step}({', '.join(f'{m}=...' for m in missing)})."
        )
    return template.format_map({k: v for k, v in inputs.items()})
