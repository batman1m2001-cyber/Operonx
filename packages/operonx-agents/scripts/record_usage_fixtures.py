"""Record one real chat completion per OpenAI-shaped gateway, for the
``Usage`` normalisation tests (tests/fixtures/*.json). Raw HTTP, so the
fixture is the gateway's own body; the request is a 1-line prompt.

    uv run python scripts/record_usage_fixtures.py
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv("/home/thanglq/callbot-wt/refactor/.env", override=False)
OUT = Path(__file__).resolve().parents[1] / "tests" / "fixtures"

ENDPOINTS = {
    "inhouse": (
        os.environ.get("LLM_API_URL") or "https://llm-callbot.edupia.com.vn:8689/v1",
        os.environ["LLM_API_KEY"],
        os.environ.get("LLM_MODEL_NAME") or "google/gemma-4-E2B-it",
    ),
    "qwen3.7-plus": (
        os.environ.get("QWEN_API_URL") or "https://llm.siraya.ai/v1",
        os.environ["QWEN_API_KEY"],
        "qwen3.7-plus",
    ),
}

for name, (url, key, model) in ENDPOINTS.items():
    body = {
        "model": model,
        "messages": [{"role": "user", "content": "Say 'ok'."}],
        "max_tokens": 5,
        "temperature": 0,
        "logprobs": True,
    }
    r = httpx.post(
        url.rstrip("/") + "/chat/completions",
        json=body,
        headers={"Authorization": f"Bearer {key}"},
        timeout=60,
    )
    r.raise_for_status()
    data = r.json()
    path = OUT / f"openai_shape_{name}.json"
    path.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n")
    print(name, r.status_code, "usage:", data.get("usage"))
