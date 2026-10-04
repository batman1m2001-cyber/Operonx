"""``timeout:`` on an ``llm:`` resource bounds each request to it.

Without it the effective limit was the shared HTTP client's 120 s read
timeout, so a gateway that accepted the connection and never answered
held the caller for two minutes — dead air on a voice call. The config
had no field for it, and an unknown key in ``resources.yaml`` is ignored,
so writing ``timeout: 2`` did nothing at all.

The test points a real backend at a local server that accepts and never
answers, and asserts the call fails near the declared bound.
"""

from __future__ import annotations

import asyncio
import time

import openai
import pytest

from operonx.providers.llms.config import LLMConfig
from operonx.providers.llms.openai import OpenAISDKModel

pytestmark = pytest.mark.unit


async def _silent_server():
    async def hold(reader, writer):
        await asyncio.sleep(30)
        writer.close()

    server = await asyncio.start_server(hold, "127.0.0.1", 0)
    return server, server.sockets[0].getsockname()[1]


def _config(port: int, **extra) -> LLMConfig:
    return LLMConfig.create_config(
        {
            "api_type": "openai",
            "api_key": "k",
            "base_url": f"http://127.0.0.1:{port}/v1",
            "model": "m",
            **extra,
        }
    )


def test_timeout_is_a_resource_field():
    assert _config(1, timeout=2.5).timeout == 2.5
    assert _config(1).timeout is None


@pytest.mark.asyncio
async def test_a_silent_gateway_fails_at_the_resource_timeout():
    server, port = await _silent_server()
    llm = OpenAISDKModel(_config(port, timeout=0.3))
    llm.client = llm.client.with_options(max_retries=0)  # the SDK's own retry would multiply it
    try:
        start = time.perf_counter()
        with pytest.raises(openai.APITimeoutError):
            await asyncio.wait_for(
                llm.generate(messages=[{"role": "user", "content": "hi"}]), timeout=5
            )
        assert time.perf_counter() - start < 1.5
    finally:
        await llm.close()
        server.close()


@pytest.mark.parametrize("api_type", ["anthropic", "azure"])
def test_every_backend_hands_its_client_the_timeout(api_type):
    """The other backends build their own client; each must read the field."""
    from operonx.providers.llms.anthropic import AnthropicModel
    from operonx.providers.llms.azure import AzureSDKModel

    base = {"api_key": "k", "model": "m", "timeout": 0.75}
    if api_type == "anthropic":
        llm = AnthropicModel(LLMConfig.create_config({"api_type": "anthropic", **base}))
        timeout = llm.client.timeout
    else:
        llm = AzureSDKModel(
            LLMConfig.create_config(
                {
                    "api_type": "azure",
                    "api_version": "2024-06-01",
                    "azure_endpoint": "https://example.invalid",
                    **base,
                }
            )
        )
        timeout = llm.http_client.timeout
    assert timeout.read == 0.75 and timeout.connect == 0.75
