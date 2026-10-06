"""Typed answers: ``native`` | ``tool`` | ``prompted``, always validated.

The shape an answer must have is a pydantic model — one given directly,
built from a :class:`Choice`, or wrapped around a plain type. How the
model is asked for it is the resource's declaration
(``structured_output:`` on its ``llm:`` block, default ``prompted``):

- ``native``: ``response_format: {type: json_schema}``. The gateway
  constrains decoding; ``inhouse`` (vLLM) enforces it.
- ``tool``: one forced call to a ``final_result`` tool whose parameters
  are the schema. The most portable; ``qwen3.7-plus`` forces the call
  but does not check its arguments.
- ``prompted``: the schema is described in the prompt and the first JSON
  object of the text is read.

Every strategy validates the answer with pydantic, because no gateway was
measured to be trustworthy on its own (``docs/AGENTS_V2_PLAN.md`` §2c).
An answer that fails is re-asked with the error, at most
``output_retries`` times, inside the model's deadline; then
:class:`~operonx_agents.OutputInvalid`.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Dict, List, Literal, Mapping, Optional, Sequence, Tuple

import pydantic
from pydantic import BaseModel, create_model

from operonx_agents.errors import OutputInvalid
from operonx_agents.model.model import Model, ModelResponse, ModelSettings
from operonx_agents.model.usage import Usage
from operonx_agents.tools.tool import model_schema

__all__ = [
    "Choice",
    "OutputResult",
    "Shape",
    "STRATEGIES",
    "ask",
    "final_tool",
    "native_format",
    "read_answer",
    "shape_for",
    "validation_errors",
]

STRATEGIES = ("native", "tool", "prompted")
FINAL_TOOL = "final_result"


class Choice:
    """One label out of a set — fixed, or read per call from an input.

    ``Choice(["agree", "busy"])`` or ``Choice(from_input="allowed_intents")``,
    where the input holds the list at run time (a per-state allow-list).
    ``field`` is the JSON key the label is answered under: ``{"intent":
    "busy"}`` for ``field="intent"``, so a prompt that already asks for
    that key keeps working.
    """

    __slots__ = ("values", "from_input", "field")

    def __init__(
        self,
        values: Optional[Sequence[str]] = None,
        *,
        from_input: Optional[str] = None,
        field: str = "value",
    ) -> None:
        if (values is None) == (from_input is None):
            raise ValueError(
                "Choice takes the labels, or from_input= naming the input that holds them "
                "at run time — exactly one of the two."
            )
        if values is not None:
            values = _labels(values, "Choice(values)")
        self.values = values
        self.from_input = from_input
        self.field = field

    def labels(self, inputs: Mapping[str, Any]) -> Tuple[str, ...]:
        if self.values is not None:
            return self.values
        if self.from_input not in inputs:
            raise KeyError(
                f"Choice(from_input={self.from_input!r}) found no input {self.from_input!r}. "
                f"Pass it to the step: step({self.from_input}=...)."
            )
        return _labels(inputs[self.from_input], f"input {self.from_input!r}")

    def __repr__(self) -> str:
        source = list(self.values) if self.values is not None else f"from_input={self.from_input!r}"
        return f"Choice({source}, field={self.field!r})"


def _labels(values: Any, where: str) -> Tuple[str, ...]:
    if isinstance(values, str) or not isinstance(values, (list, tuple, set, frozenset)):
        raise TypeError(f"{where} must be a list of labels, got {type(values).__name__}")
    labels = tuple(dict.fromkeys(str(v) for v in values))
    if not labels:
        raise ValueError(
            f"{where} is empty: no answer could be valid. Give at least one label, or map "
            "an empty set to a value before the step."
        )
    return labels


@dataclass(frozen=True)
class Shape:
    """What one call must answer.

    Attributes:
        kind: ``choice``, ``model`` (a pydantic model given), ``scalar``
            (a plain type, wrapped) or ``text`` (no structure).
        model: The pydantic model the answer validates against.
        field: For ``choice``/``scalar``, the key whose value is the answer.
        name: The schema's name in the request.
        labels: A choice's labels.
    """

    kind: str
    model: Optional[type] = None
    field: Optional[str] = None
    name: str = "answer"
    labels: Tuple[str, ...] = ()

    def schema(self) -> Dict[str, Any]:
        if self.kind == "choice":
            # Spelled out: pydantic gives one label as "const", which not
            # every constrained decoder reads; "enum" every one does.
            return {
                "type": "object",
                "properties": {self.field: {"type": "string", "enum": list(self.labels)}},
                "required": [self.field],
                "additionalProperties": False,
            }
        out = model_schema(self.model)
        out["additionalProperties"] = False
        return out

    def value(self, validated: BaseModel) -> Any:
        return getattr(validated, self.field) if self.field else validated


def shape_for(output: Any, inputs: Mapping[str, Any] = {}) -> Shape:  # noqa: B006
    """The :class:`Shape` of ``output`` for one call's ``inputs``."""
    if output is str or output is None:
        return Shape(kind="text")
    if isinstance(output, Choice):
        labels = output.labels(inputs)
        return Shape(
            kind="choice",
            model=_choice_model(labels, output.field),
            field=output.field,
            name=output.field,
            labels=labels,
        )
    if isinstance(output, type) and issubclass(output, BaseModel):
        return Shape(kind="model", model=output, name=output.__name__)
    try:
        model = create_model("Answer", value=(output, ...))
    except Exception as exc:
        raise TypeError(
            f"output={output!r} is not a type an answer can have. Use str, a Choice, a "
            "pydantic model, or a type pydantic can validate (int, list[str], Literal[...])."
        ) from exc
    return Shape(kind="scalar", model=model, field="value", name="answer")


@lru_cache(maxsize=256)
def _choice_model(labels: Tuple[str, ...], field: str) -> type:
    """Built once per label set: a state machine reuses a few sets."""
    return create_model("Choice", **{field: (Literal[labels], ...)})  # type: ignore[valid-type]


@dataclass(frozen=True)
class OutputResult:
    """A validated answer.

    Attributes:
        value: The answer — the label, the model instance, the scalar, or
            the text.
        response: The model response it came from (the last ask).
        usage: Every request it took, re-asks included.
        confidence: For a choice, the model's probability of the label it
            gave, from token logprobs; ``None`` when there are none.
        asks: Requests made: 1, plus one per re-ask.
        strategy: How it was asked.
    """

    value: Any
    response: ModelResponse
    usage: Usage
    confidence: Optional[float]
    asks: int
    strategy: str


async def ask(
    model: Model,
    messages: List[Dict[str, Any]],
    shape: Shape,
    *,
    strategy: Optional[str] = None,
    output_retries: int = 1,
    settings: Optional[ModelSettings] = None,
) -> OutputResult:
    """Ask ``model`` for an answer of ``shape`` and validate it, within the
    model's deadline.

    ``strategy`` defaults to what the primary resource declares; every
    fallback must declare the same, since one request goes to each.

    Raises:
        OutputInvalid: still invalid after ``output_retries`` re-asks.
        ModelTimeout / ModelError / ModelRefused: from the model.
    """
    if output_retries < 0:
        raise ValueError(f"output_retries must be 0 or more, got {output_retries}")
    strategy = strategy or _declared(model)
    if strategy not in STRATEGIES:
        raise ValueError(f"strategy {strategy!r} is not one of {list(STRATEGIES)}")
    convo = list(messages)
    usage = Usage()
    async with model.bounded():
        for attempt in range(output_retries + 1):
            reply = await model.request_unbounded(
                convo, settings=settings, **_request(shape, strategy, convo)
            )
            usage = usage + reply.usage
            if shape.kind == "text":
                return OutputResult(reply.content, reply, usage, None, attempt + 1, strategy)
            raw, error = read_answer(reply, strategy)
            if error is None:
                try:
                    validated = shape.model.model_validate(raw)
                except pydantic.ValidationError as exc:
                    error = validation_errors(exc)
                else:
                    value = shape.value(validated)
                    confidence = _confidence(reply, value) if shape.kind == "choice" else None
                    return OutputResult(value, reply, usage, confidence, attempt + 1, strategy)
            if attempt == output_retries:
                raise OutputInvalid(
                    f"the answer of {model.resource!r} did not validate after "
                    f"{output_retries} re-ask(s): {error}. Raise output_retries, tighten the "
                    "prompt, or map it to a value: llm_step(on_invalid=...).",
                    answer=raw if raw is not None else reply.content,
                    error=error,
                )
            convo = convo + _reask(reply, strategy, error)
    raise AssertionError("unreachable")  # pragma: no cover


def _declared(model: Model) -> str:
    strategies = {r: model.structured_output(r) for r in model.resources}
    if len(set(strategies.values())) > 1:
        raise ValueError(
            f"{model!r}: its resources declare different structured_output values "
            f"({strategies}), and one request is sent to each of them in turn. Use resources "
            "that declare the same, or pass strategy= explicitly."
        )
    return strategies[model.resource]


def _request(shape: Shape, strategy: str, convo: List[Dict[str, Any]]) -> Dict[str, Any]:
    if shape.kind == "text":
        return {}
    if strategy == "native":
        return {"response_format": native_format(shape)}
    if strategy == "tool":
        return {
            "tools": [final_tool(shape)],
            "tool_choice": {"type": "function", "function": {"name": FINAL_TOOL}},
        }
    schema = shape.schema()
    # prompted: the schema goes in the conversation, on the last user turn.
    note = (
        "\n\nAnswer with one JSON object that matches this JSON Schema, and nothing else:\n"
        + json.dumps(schema, ensure_ascii=False)
    )
    for i in range(len(convo) - 1, -1, -1):
        if convo[i].get("role") == "user" and isinstance(convo[i].get("content"), str):
            convo[i] = {**convo[i], "content": convo[i]["content"] + note}
            break
    else:
        convo.append({"role": "user", "content": note.strip()})
    return {}


def native_format(shape: Shape) -> Dict[str, Any]:
    """The ``response_format`` asking for ``shape`` (the ``native`` strategy)."""
    schema = shape.schema()
    return {
        "type": "json_schema",
        "json_schema": {"name": shape.name, "schema": schema, "strict": _strict(schema)},
    }


def final_tool(shape: Shape, description: str = "Give the final answer.") -> Dict[str, Any]:
    """The ``final_result`` tool whose arguments are ``shape`` (the ``tool``
    strategy)."""
    return {
        "type": "function",
        "function": {"name": FINAL_TOOL, "description": description, "parameters": shape.schema()},
    }


def _strict(schema: Dict[str, Any]) -> bool:
    """OpenAI's strict mode needs every property required; say so only
    when it holds, rather than send a schema strict mode would reject."""
    props = schema.get("properties") or {}
    return set(schema.get("required") or ()) == set(props) and "$defs" not in schema


def read_answer(reply: ModelResponse, strategy: str) -> Tuple[Any, Optional[str]]:
    """``(object, None)`` or ``(None, why it could not be read)``: the
    ``final_result`` call's arguments for ``tool``, else the first JSON
    object of the text."""
    if strategy == "tool":
        for call in reply.tool_calls:
            if call["name"] == FINAL_TOOL:
                if isinstance(call["args"], dict):
                    return call["args"], None
                return None, f"the {FINAL_TOOL} arguments are not a JSON object"
        return None, f"no call to {FINAL_TOOL}; answer by calling it"
    obj = _first_json_object(reply.content)
    if obj is None:
        return None, "the answer is not a JSON object"
    return obj, None


_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def _first_json_object(text: str) -> Optional[dict]:
    text = (text or "").strip()
    candidates = [text, *(m.group(1).strip() for m in _FENCE.finditer(text))]
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(value, dict):
            return value
    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", text):
        try:
            value, _ = decoder.raw_decode(text, match.start())
        except ValueError:
            continue
        if isinstance(value, dict):
            return value
    return None


def validation_errors(exc: pydantic.ValidationError) -> str:
    """``field: message; ...`` — each bad field by name."""
    parts = []
    for err in exc.errors(include_url=False):
        where = ".".join(str(p) for p in err["loc"]) or "(answer)"
        parts.append(f"{where}: {err['msg']}")
    return "; ".join(parts)


def _reask(reply: ModelResponse, strategy: str, error: str) -> List[Dict[str, Any]]:
    fix = f"Your answer did not match the required shape: {error}. Answer again, corrected."
    if strategy == "tool" and reply.tool_calls:
        # Every call must be answered before the model speaks again.
        return [
            {"role": "assistant", "content": reply.content or "", "tool_calls": reply.tool_calls},
            *({"role": "tool", "tool_call_id": c["id"], "content": fix} for c in reply.tool_calls),
        ]
    return [
        {"role": "assistant", "content": reply.content or ""},
        {"role": "user", "content": fix},
    ]


def _confidence(reply: ModelResponse, label: str) -> Optional[float]:
    """P(label) from the tokens that spell it in the answer text.

    The tokens' text must spell the answer (a stop token after it is
    fine), else the offsets cannot be trusted and there is no confidence.
    The label is found where it is the value of a JSON string
    (``"busy"``); the probability is the product of the tokens overlapping
    it, given what came before.
    """
    tokens = reply.logprobs
    if not tokens or not reply.content:
        return None
    pieces = [t.get("token") or "" for t in tokens]
    # vLLM ends the list with the stop token's text (gemma: "<eos>"),
    # which the answer does not contain.
    if not "".join(pieces).startswith(reply.content):
        return None
    start = reply.content.find(json.dumps(label, ensure_ascii=False))
    if start < 0:
        return None
    start, end = start + 1, start + 1 + len(label)
    total, offset, seen = 0.0, 0, False
    for piece, token in zip(pieces, tokens):
        lo, hi = offset, offset + len(piece)
        offset = hi
        if hi > start and lo < end:
            logprob = token.get("logprob")
            if logprob is None:
                return None
            total += float(logprob)
            seen = True
    return math.exp(total) if seen else None
