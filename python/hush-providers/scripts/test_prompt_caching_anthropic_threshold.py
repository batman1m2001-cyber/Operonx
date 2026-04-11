"""Probe the Anthropic cache minimum-token threshold.

Tests two older Haiku models (both documented at 2048-token minimum)
with three prompt sizes each:

    - below    (~1500 tokens) -> expect silent miss
    - just above (~2200 tokens) -> expect cache write then read
    - comfortable (~4000 tokens) -> expect clean write then read

For every size we make TWO back-to-back calls and report both
cache_creation_input_tokens and cache_read_input_tokens.

Usage:
    uv run --with anthropic python scripts/test_prompt_caching_anthropic_threshold.py
"""

from __future__ import annotations

import asyncio
import os

from _common import big_static_context, load_env, section


async def probe(client, model: str, target_tokens: int) -> None:
    context = big_static_context(target_tokens=target_tokens)
    print(f"\n--- {model} @ ~{target_tokens} target tokens ---")

    async def call(label: str, question: str) -> None:
        resp = await client.messages.create(
            model=model,
            max_tokens=10,
            system=[
                {
                    "type": "text",
                    "text": context,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": question}],
        )
        u = resp.usage
        prefix_tokens = (
            u.input_tokens
            + (u.cache_creation_input_tokens or 0)
            + (u.cache_read_input_tokens or 0)
        )
        verdict = (
            "CACHED (read)"
            if (u.cache_read_input_tokens or 0) > 0
            else "WROTE cache"
            if (u.cache_creation_input_tokens or 0) > 0
            else "NOT CACHED (silent miss)"
        )
        print(
            f"  {label}: prefix={prefix_tokens:5d}  "
            f"write={u.cache_creation_input_tokens or 0:5d}  "
            f"read={u.cache_read_input_tokens or 0:5d}  "
            f"input={u.input_tokens:3d}  -> {verdict}"
        )

    await call("call-1 (cold)", "Reply with: one")
    await asyncio.sleep(2)
    await call("call-2 (warm)", "Reply with: two")


async def main() -> None:
    load_env()
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise SystemExit("ANTHROPIC_API_KEY not set in .env")

    from anthropic import AsyncAnthropic

    client = AsyncAnthropic(api_key=api_key)

    models = [
        "claude-3-5-haiku-latest",  # 3.5 Haiku — docs: 2048 min
        "claude-3-haiku-20240307",  # 3 Haiku — docs: 2048 min
    ]
    # Our char/4 estimator undercounts; these hit actual token counts of
    # roughly 1288, 2050, 2570, 3417 respectively.
    sizes = [1500, 2400, 3000, 4000]

    for model in models:
        section(f"Anthropic threshold probe — {model}")
        for size in sizes:
            try:
                await probe(client, model, size)
            except Exception as e:
                print(f"  ERROR at {size} tokens: {e}")
            await asyncio.sleep(1)


if __name__ == "__main__":
    asyncio.run(main())
