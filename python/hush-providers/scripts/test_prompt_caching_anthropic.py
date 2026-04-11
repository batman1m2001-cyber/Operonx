"""Verify Anthropic explicit prompt caching.

First call should report cache_creation_input_tokens > 0 (write).
Second call should report cache_read_input_tokens > 0 (hit) and
cache_creation_input_tokens == 0.

Usage:
    uv run python scripts/test_prompt_caching_anthropic.py
"""

from __future__ import annotations

import asyncio
import os

from _common import big_static_context, load_env, section, show_usage


async def main() -> None:
    load_env()
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise SystemExit("ANTHROPIC_API_KEY not set in .env")

    try:
        from anthropic import AsyncAnthropic
    except ImportError:
        raise SystemExit(
            "anthropic SDK not installed. Add it to dev deps or run "
            "`uv pip install anthropic` in this project."
        )

    client = AsyncAnthropic(api_key=api_key)
    # Sonnet has a 2048-token minimum; older Haiku also cheap. Default to
    # claude-3-5-haiku-latest which has a 2048-token minimum.
    model = os.environ.get("ANTHROPIC_TEST_MODEL", "claude-haiku-4-5")
    context = big_static_context(target_tokens=8000)

    section(f"Anthropic prompt caching — model={model}")
    print(
        "Explicit cache_control on system block; reads/writes reported "
        "as usage.cache_read_input_tokens / cache_creation_input_tokens"
    )

    async def call(question: str, label: str) -> None:
        resp = await client.messages.create(
            model=model,
            max_tokens=50,
            system=[
                {
                    "type": "text",
                    "text": context,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": question}],
        )
        usage = resp.usage.model_dump() if resp.usage else {}
        show_usage(label, usage)
        write = usage.get("cache_creation_input_tokens", 0)
        read = usage.get("cache_read_input_tokens", 0)
        print(f"  -> cache_creation={write}  cache_read={read}")

    await call("Reply with the single word: first", "call-1 (cold)")
    await asyncio.sleep(2)
    await call("Reply with the single word: second", "call-2 (warm)")


if __name__ == "__main__":
    asyncio.run(main())
