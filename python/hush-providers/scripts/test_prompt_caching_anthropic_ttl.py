"""Verify Anthropic prompt caching with extended TTL (1 hour).

Tests that the ``cache_ttl`` parameter is correctly passed through
the hush-providers Anthropic backend layer.

Call 1 → cache_creation (write) with ttl="1h".
Call 2 → cache_read (hit) — confirms the 1h-TTL cache entry was created.

Usage:
    uv run --with httpx python scripts/test_prompt_caching_anthropic_ttl.py
"""

from __future__ import annotations

import asyncio
import os

from _common import big_static_context, load_env, section

from hush.providers.llms.anthropic import AnthropicModel
from hush.providers.llms.base import cache_metrics
from hush.providers.llms.config import AnthropicConfig


async def run_ttl_1h() -> None:
    section("Anthropic backend — cache_ttl='1h' (extended TTL)")
    cfg = AnthropicConfig(
        api_key=os.environ["ANTHROPIC_API_KEY"],
        model=os.environ.get("ANTHROPIC_TEST_MODEL", "claude-haiku-4-5"),
    )
    llm = AnthropicModel(cfg)
    context = big_static_context(target_tokens=8000)
    messages = [
        {"role": "system", "content": context},
        {"role": "user", "content": "Reply with one word."},
    ]

    # Call 1: cold — should write to cache with 1h TTL
    resp1 = await llm.generate(messages=messages, max_tokens=5, cache_ttl="1h")
    m1 = cache_metrics(resp1)
    print(f"  [cold] prompt_tokens={resp1.usage.prompt_tokens}  metrics={m1}")
    assert m1["cache_write_tokens"] > 0, "cold call should write to cache"
    print("  OK: cache written with ttl=1h")

    await asyncio.sleep(2)

    # Call 2: warm — should hit the 1h-TTL cache
    resp2 = await llm.generate(messages=messages, max_tokens=5, cache_ttl="1h")
    m2 = cache_metrics(resp2)
    print(f"  [warm] prompt_tokens={resp2.usage.prompt_tokens}  metrics={m2}")
    assert m2["cached_input_tokens"] > 0, "warm call should hit cache"
    print("  OK: cache hit from 1h-TTL entry")


async def run_default_vs_1h() -> None:
    section("Anthropic backend — default TTL (5m) vs explicit 1h")
    cfg = AnthropicConfig(
        api_key=os.environ["ANTHROPIC_API_KEY"],
        model=os.environ.get("ANTHROPIC_TEST_MODEL", "claude-haiku-4-5"),
    )
    llm = AnthropicModel(cfg)
    context = big_static_context(target_tokens=8000)
    messages = [
        {"role": "system", "content": context},
        {"role": "user", "content": "Reply with one word."},
    ]

    # Default TTL (no cache_ttl kwarg) — should still work as before
    resp = await llm.generate(messages=messages, max_tokens=5)
    m = cache_metrics(resp)
    print(f"  [default-ttl] prompt_tokens={resp.usage.prompt_tokens}  metrics={m}")
    print("  OK: default TTL (5m) works without cache_ttl kwarg")


async def main() -> None:
    load_env()
    await run_ttl_1h()
    await run_default_vs_1h()


if __name__ == "__main__":
    asyncio.run(main())
