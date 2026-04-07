"""06 Tracing / Local — Run with LocalTracer.

Traces written to ~/.hush/traces/{request_id}.json

Chạy: cd examples && uv run python ex06_tracing/local/demo.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import asyncio

from hush.core import Hush
from hush.core.tracing import LocalTracer

from ex06_tracing.local.workflow import build_text_analyzer

EXAMPLES_DIR = Path(__file__).resolve().parents[2]
ENV_FILE = str(EXAMPLES_DIR / ".env")
RESOURCES_FILE = str(EXAMPLES_DIR.parent / "resources.yaml")


async def main():
    print("=" * 50)
    print("1. LocalTracer — Python mode")
    print("=" * 50)

    tracer = LocalTracer(tags=["tutorial", "tracing-demo"])
    engine = Hush(build_text_analyzer(), env=ENV_FILE, resources=RESOURCES_FILE, tracer=tracer)

    result = await engine.run(
        inputs={"text": "Hush la async workflow engine cho GenAI applications"},
        user_id="user-123",
        session_id="session-456",
    )
    print(f"  Word count: {result['word_count']}")
    print(f"  Category:   {result['category']}")

    state = result["$state"]
    print(f"  User ID:    {state.user_id}")
    print(f"  Request ID: {state.request_id}")
    print(f"  Tags:       {state.tags}")
    print("  Traces at:  ~/.hush/traces/")

    print()
    print("=" * 50)
    print("2. Multiple users")
    print("=" * 50)

    test_cases = [
        {"text": "Hello world", "user_id": "alice", "session_id": "s1"},
        {
            "text": "Day la mot van ban dai hon de test dynamic tags va phan loai trong Hush",
            "user_id": "bob",
            "session_id": "s2",
        },
    ]

    for tc in test_cases:
        result = await engine.run(
            inputs={"text": tc["text"]},
            user_id=tc["user_id"],
            session_id=tc["session_id"],
        )
        state = result["$state"]
        print(f"  User={tc['user_id']}: words={result['word_count']}, tags={state.tags}")


if __name__ == "__main__":
    asyncio.run(main())
