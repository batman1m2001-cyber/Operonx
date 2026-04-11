"""End-to-end integration test for auto-prompt-caching in BaseLLM.

Exercises both OpenAI and Anthropic backends through the hush-providers
layer (not raw APIs) to confirm:

  1. cache=True is the default.
  2. cached_tokens / cache_write_tokens are surfaced uniformly via
     base.cache_metrics() on the ChatCompletion result.
  3. Short prompts fall below the Anthropic threshold and silently skip
     cache injection (no error, no cache hit).

Usage:
    uv run --with anthropic --with httpx \
        python scripts/test_backend_caching_integration.py
"""

from __future__ import annotations

import asyncio
import os

from _common import big_static_context, load_env, section

from hush.providers.llms.anthropic import AnthropicModel
from hush.providers.llms.base import cache_metrics
from hush.providers.llms.config import AnthropicConfig, OpenAIConfig
from hush.providers.llms.openai import OpenAISDKModel


async def run_openai() -> None:
    section("OpenAI backend — automatic caching via OpenAI SDK")
    cfg = OpenAIConfig(
        api_key=os.environ["OPENAI_API_KEY"],
        base_url="https://api.openai.com/v1",
        model="gpt-4o-mini",
    )
    llm = OpenAISDKModel(cfg)
    context = big_static_context(target_tokens=5000)
    messages = [
        {"role": "system", "content": context},
        {"role": "user", "content": "Reply with one word."},
    ]
    for label in ("cold", "warm"):
        resp = await llm.generate(messages=messages, max_tokens=5)
        print(f"  [{label}] prompt_tokens={resp.usage.prompt_tokens}  "
              f"metrics={cache_metrics(resp)}")
    await llm.close()


async def run_anthropic_long() -> None:
    section("Anthropic backend — long prompt, caching should engage")
    cfg = AnthropicConfig(
        api_key=os.environ["ANTHROPIC_API_KEY"],
        model="claude-3-haiku-20240307",
    )
    llm = AnthropicModel(cfg)
    context = big_static_context(target_tokens=8000)
    messages = [
        {"role": "system", "content": context},
        {"role": "user", "content": "Reply with one word."},
    ]
    for idx, label in enumerate(("cold", "warm")):
        if idx > 0:
            # Anthropic's cache write is asynchronous; a back-to-back call
            # can race against the first write and get another miss. Two
            # seconds is empirically enough for propagation.
            await asyncio.sleep(2)
        resp = await llm.generate(messages=messages, max_tokens=5)
        print(
            f"  [{label}] prompt_tokens={resp.usage.prompt_tokens}  "
            f"metrics={cache_metrics(resp)}"
        )


async def run_anthropic_short() -> None:
    section("Anthropic backend — short prompt, caching should silently skip")
    cfg = AnthropicConfig(
        api_key=os.environ["ANTHROPIC_API_KEY"],
        model="claude-3-haiku-20240307",
    )
    llm = AnthropicModel(cfg)
    # Below the safe threshold — estimator will reject cache injection.
    messages = [
        {"role": "system", "content": "You are a concise assistant."},
        {"role": "user", "content": "Say hi."},
    ]
    resp = await llm.generate(messages=messages, max_tokens=5)
    m = cache_metrics(resp)
    print(f"  prompt_tokens={resp.usage.prompt_tokens}  metrics={m}")
    assert m["cached_input_tokens"] == 0 and m["cache_write_tokens"] == 0, (
        "short prompt must not attempt to cache"
    )
    print("  OK: no cache attempted (as expected)")


async def main() -> None:
    load_env()
    await run_openai()
    await run_anthropic_long()
    await run_anthropic_short()


if __name__ == "__main__":
    asyncio.run(main())
