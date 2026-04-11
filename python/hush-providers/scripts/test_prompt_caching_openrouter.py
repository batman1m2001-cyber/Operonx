"""Verify OpenRouter prompt caching for two routes:

1. OpenAI-routed model (automatic caching, no cache_control needed).
2. Anthropic-routed model (explicit cache_control on system block).

OpenRouter exposes an OpenAI-compatible response shape, so cached reads
land in usage.prompt_tokens_details.cached_tokens for both routes.
For Anthropic via OpenRouter, cache writes are also reported via
usage.prompt_tokens_details.cache_write_tokens on the first call.

Usage:
    uv run python scripts/test_prompt_caching_openrouter.py
"""

from __future__ import annotations

import asyncio
import json
import os

import httpx
from _common import big_static_context, load_env, section, show_usage

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


async def post(client: httpx.AsyncClient, api_key: str, body: dict) -> dict:
    resp = await client.post(
        OPENROUTER_URL,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/hush-ai/hush",
            "X-Title": "hush prompt-caching test",
        },
        json=body,
    )
    if resp.status_code >= 400:
        raise SystemExit(f"OpenRouter error {resp.status_code}: {resp.text}")
    return resp.json()


def summarize(label: str, payload: dict) -> None:
    usage = payload.get("usage") or {}
    show_usage(label, usage)
    details = usage.get("prompt_tokens_details") or {}
    print(
        f"  -> cached_tokens={details.get('cached_tokens', 0)}  "
        f"cache_write_tokens={details.get('cache_write_tokens', 0)}  "
        f"cache_discount={usage.get('cache_discount', 0)}"
    )


async def run_openai_route(client: httpx.AsyncClient, api_key: str) -> None:
    model = os.environ.get("OPENROUTER_OPENAI_MODEL", "openai/gpt-4o-mini")
    context = big_static_context(target_tokens=5000)
    section(f"OpenRouter (automatic) — model={model}")
    print("OpenAI-routed: caching is automatic, no cache_control needed.")

    for idx, question in enumerate(["Reply with: first", "Reply with: second"], start=1):
        body = {
            "model": model,
            "max_tokens": 50,
            "usage": {"include": True},
            "messages": [
                {"role": "system", "content": context},
                {"role": "user", "content": question},
            ],
        }
        summarize(f"call-{idx}", await post(client, api_key, body))


async def run_anthropic_route(client: httpx.AsyncClient, api_key: str) -> None:
    model = os.environ.get("OPENROUTER_ANTHROPIC_MODEL", "anthropic/claude-3.5-haiku")
    context = big_static_context(target_tokens=5000)
    section(f"OpenRouter (explicit) — model={model}")
    print("Anthropic-routed: must pass cache_control on the block to cache.")

    for idx, question in enumerate(["Reply with: first", "Reply with: second"], start=1):
        body = {
            "model": model,
            "max_tokens": 50,
            "usage": {"include": True},
            "messages": [
                {
                    "role": "system",
                    "content": [
                        {
                            "type": "text",
                            "text": context,
                            "cache_control": {"type": "ephemeral"},
                        }
                    ],
                },
                {"role": "user", "content": question},
            ],
        }
        summarize(f"call-{idx}", await post(client, api_key, body))


async def main() -> None:
    load_env()
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise SystemExit("OPENROUTER_API_KEY not set in .env")

    async with httpx.AsyncClient(timeout=60.0) as client:
        await run_openai_route(client, api_key)
        await run_anthropic_route(client, api_key)


if __name__ == "__main__":
    asyncio.run(main())
    _ = json  # silence linter if unused
