"""Verify OpenAI automatic prompt caching.

Runs the same long-prefix request twice. First call writes the cache,
second call should report non-zero `prompt_tokens_details.cached_tokens`.

Usage:
    uv run python scripts/test_prompt_caching_openai.py
"""

from __future__ import annotations

import asyncio
import os

from _common import big_static_context, load_env, section, show_usage


async def main() -> None:
    load_env()
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise SystemExit("OPENAI_API_KEY not set in .env")

    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=api_key)
    model = os.environ.get("OPENAI_TEST_MODEL", "gpt-4o-mini")
    context = big_static_context(target_tokens=5000)

    section(f"OpenAI prompt caching — model={model}")
    print("Automatic caching: minimum 1024 tokens; reads reported as "
          "usage.prompt_tokens_details.cached_tokens")

    async def call(question: str, label: str) -> None:
        resp = await client.chat.completions.create(
            model=model,
            max_tokens=50,
            messages=[
                {"role": "system", "content": context},
                {"role": "user", "content": question},
            ],
        )
        usage = resp.usage.model_dump() if resp.usage else {}
        show_usage(label, usage)
        details = usage.get("prompt_tokens_details") or {}
        cached = details.get("cached_tokens", 0)
        print(f"  -> cached_tokens = {cached}")

    await call("Reply with the single word: first", "call-1 (cold)")
    await call("Reply with the single word: second", "call-2 (warm)")


if __name__ == "__main__":
    asyncio.run(main())
