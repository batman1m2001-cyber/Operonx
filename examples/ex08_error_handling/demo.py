"""08 Error Handling — Run workflows with Hush Python engine.

Examples 1-3: pure compute (no API keys).
Example 4: requires OPENAI_API_KEY (LLM fallback).

Chạy: cd examples && uv run python ex08_error_handling/demo.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import asyncio

from hush.core import Hush

from ex08_error_handling.workflow import (
    build_error_capture,
    build_error_routing,
    build_llm_fallback,
    build_retry_fallback,
)

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

EXAMPLES_DIR = Path(__file__).resolve().parents[1]
ENV_FILE = str(EXAMPLES_DIR / ".env")
RESOURCES_FILE = str(EXAMPLES_DIR.parent / "resources.yaml")


async def main():
    # 1. Error capture — workflow doesn't crash
    print("=" * 50)
    print("1. Error capture trong state")
    print("=" * 50)
    result = await Hush(build_error_capture(), env=ENV_FILE, resources=RESOURCES_FILE).run(inputs={})
    state = result["$state"]
    error = state["error-capture.fail", "error", None]
    print(f"  Error captured: {error is not None}")
    print("  Error type: ZeroDivisionError" if error else "  No error")

    # 2. Error routing — if_(success) branches
    print()
    print("=" * 50)
    print("2. Error routing với if_()")
    print("=" * 50)
    engine = Hush(build_error_routing(), env=ENV_FILE, resources=RESOURCES_FILE)
    result = await engine.run(inputs={"a": 10, "b": 3})
    print(f"  10 / 3 → {result['output']}")
    result = await engine.run(inputs={"a": 10, "b": 0})
    print(f"  10 / 0 → {result['output']}")

    # 3. Retry + fallback
    print()
    print("=" * 50)
    print("3. Retry + Graceful degradation")
    print("=" * 50)
    engine = Hush(build_retry_fallback(), env=ENV_FILE, resources=RESOURCES_FILE)
    for query in ["What is AI?", "What is ML?"]:
        result = await engine.run(inputs={"query": query})
        print(f"  Query: {query}")
        print(f"    Output: {result['output']}")
        print(f"    Used fallback: {result['used_fallback']}")

    # 4. LLM fallback chain
    print()
    print("=" * 50)
    print("4. LLM Fallback chain")
    print("=" * 50)
    result = await Hush(build_llm_fallback(), env=ENV_FILE, resources=RESOURCES_FILE).run(inputs={"query": "What is Python?"})
    print(f"  Answer: {result['answer'][:80]}...")
    print(f"  Model used: {result['model']}")


if __name__ == "__main__":
    asyncio.run(main())
