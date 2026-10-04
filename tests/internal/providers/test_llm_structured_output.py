"""How a resource does structured output is declared on it, and a backend
that cannot honour ``response_format`` says so instead of dropping it.

Gateways disagree (measured, ``docs/AGENTS_V2_PLAN.md`` §2c): vLLM
enforces ``json_schema``; Siraya's ``qwen3.7-plus`` constrains a valid
schema but ignores an invalid one; others 404. A guessed strategy can
give an unconstrained answer that looks constrained, so an ``llm:``
resource declares ``structured_output: native | tool | prompted`` and the
layer above reads it. The field did not exist: ``resources.yaml`` keys a
config does not know are ignored, so the declaration vanished.

The Anthropic backend builds its own request and had no line for
``response_format``: a ``json_schema`` sent there was dropped and the
answer came back unconstrained, with no error. It now refuses, naming the
fix.
"""

from __future__ import annotations

import pydantic
import pytest

from operonx.providers.llms.config import LLMConfig

pytestmark = pytest.mark.unit

OPENAI = {"api_type": "openai", "api_key": "k", "base_url": "http://x.invalid/v1", "model": "m"}
SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "intent",
        "strict": True,
        "schema": {"type": "object", "properties": {"intent": {"enum": ["a", "b"]}}},
    },
}


def test_structured_output_is_declared_per_resource():
    assert LLMConfig.create_config({**OPENAI, "structured_output": "native"}).structured_output == (
        "native"
    )
    assert LLMConfig.create_config({**OPENAI, "structured_output": "tool"}).structured_output == (
        "tool"
    )


def test_the_default_is_prompted():
    """The one strategy every gateway can do; anything stronger is declared."""
    assert LLMConfig.create_config(dict(OPENAI)).structured_output == "prompted"


def test_an_unknown_strategy_is_refused():
    with pytest.raises(pydantic.ValidationError, match="structured_output"):
        LLMConfig.create_config({**OPENAI, "structured_output": "json"})


def test_openai_backend_passes_json_schema_through():
    from types import SimpleNamespace

    from operonx.providers.llms.base import BaseLLM

    params = BaseLLM._prepare_params(
        SimpleNamespace(resolve_image_paths=lambda m: m),
        model="m",
        messages=[{"role": "user", "content": "hi"}],
        stream=False,
        response_format=SCHEMA,
    )
    assert params["response_format"] == SCHEMA


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
async def test_anthropic_refuses_a_response_format_it_would_drop(stream):
    from operonx.providers.llms.anthropic import AnthropicModel

    llm = AnthropicModel(
        LLMConfig.create_config(
            {"api_type": "anthropic", "api_key": "k", "base_url": "http://127.0.0.1:9"}
        )
    )
    messages = [{"role": "user", "content": "hi"}]
    with pytest.raises(ValueError, match="structured_output: tool"):
        if stream:
            async for _ in llm.stream(messages=messages, response_format=SCHEMA):
                pass
        else:
            await llm.generate(messages=messages, response_format=SCHEMA)


def test_anthropic_cannot_declare_native():
    with pytest.raises(pydantic.ValidationError, match="structured_output: tool"):
        LLMConfig.create_config(
            {"api_type": "anthropic", "api_key": "k", "structured_output": "native"}
        )
