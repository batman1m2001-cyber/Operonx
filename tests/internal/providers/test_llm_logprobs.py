"""``logprobs=True`` comes back on every path that can carry it.

A non-streamed answer returned them in ``extras["logprobs"]``; a streamed
one always said ``None``, although the provider sends them on each chunk
— the accumulator never read them. Azure's request builder kept a
whitelist without ``logprobs``/``top_logprobs``, so asking for them there
was silently a request without them.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from operonx.providers.ops.llm import LLMOp

pytestmark = pytest.mark.unit


def _token(token, logprob):
    return SimpleNamespace(
        model_dump=lambda: {"token": token, "logprob": logprob, "bytes": None, "top_logprobs": []}
    )


def _chunk(content, *tokens, finish_reason=None):
    delta = SimpleNamespace(content=content, tool_calls=None, reasoning_content=None, refusal=None)
    logprobs = SimpleNamespace(content=list(tokens)) if tokens else None
    choice = SimpleNamespace(delta=delta, finish_reason=finish_reason, logprobs=logprobs)
    return SimpleNamespace(choices=[choice], usage=None)


def test_a_stream_returns_the_logprobs_of_every_chunk():
    acc = LLMOp._new_stream_acc()
    LLMOp._process_chunk(_chunk('{"intent"', _token('{"', -0.01), _token("intent", -0.02)), acc)
    LLMOp._process_chunk(_chunk(': "agree"}', _token("agree", -0.3), finish_reason="stop"), acc)
    op = LLMOp(name="llm", resource="r")
    op._llms = [SimpleNamespace(config=SimpleNamespace())]
    final = op._stream_final(acc, "r")
    tokens = final["extras"]["logprobs"]["content"]
    assert [t["token"] for t in tokens] == ['{"', "intent", "agree"]
    assert tokens[2]["logprob"] == -0.3


def test_a_stream_without_logprobs_still_says_none():
    acc = LLMOp._new_stream_acc()
    LLMOp._process_chunk(_chunk("hi", finish_reason="stop"), acc)
    op = LLMOp(name="llm", resource="r")
    op._llms = [SimpleNamespace(config=SimpleNamespace())]
    assert op._stream_final(acc, "r")["extras"]["logprobs"] is None


def test_azure_sends_logprobs():
    from operonx.providers.llms.azure import AzureSDKModel
    from operonx.providers.llms.config import AzureConfig

    llm = AzureSDKModel(
        AzureConfig(
            api_key="k", api_version="2024-06-01", azure_endpoint="https://x.invalid", model="m"
        )
    )
    params = llm._prepare_params(
        model="m",
        messages=[{"role": "user", "content": "hi"}],
        stream=False,
        logprobs=True,
        top_logprobs=3,
    )
    assert params["logprobs"] is True and params["top_logprobs"] == 3
