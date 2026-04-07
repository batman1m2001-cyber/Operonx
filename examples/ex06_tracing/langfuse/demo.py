"""06 Tracing / Langfuse — Run with LangfuseTracer.

Needs: LANGFUSE_HUSH_PUBLIC_KEY, LANGFUSE_HUSH_SECRET_KEY, LANGFUSE_HUSH_BASE_URL in .env

Chạy: cd examples && uv run python ex06_tracing/langfuse/demo.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import asyncio
import os

from ex06_tracing.langfuse.workflow import build_text_analyzer

EXAMPLES_DIR = Path(__file__).resolve().parents[2]
ENV_FILE = str(EXAMPLES_DIR / ".env")
RESOURCES_FILE = str(EXAMPLES_DIR.parent / "resources.yaml")


def _make_tracer():
    """Create LangfuseTracer using LANGFUSE_HUSH_* keys."""
    from hush.telemetry import LangfuseTracer
    from hush.telemetry.backends.langfuse import LangfuseConfig

    public_key = os.environ.get("LANGFUSE_HUSH_PUBLIC_KEY")
    secret_key = os.environ.get("LANGFUSE_HUSH_SECRET_KEY")
    base_url = os.environ.get("LANGFUSE_HUSH_BASE_URL", "https://cloud.langfuse.com")

    if not public_key or not secret_key:
        import warnings

        warnings.warn("LANGFUSE_HUSH_* keys not set — running without Langfuse tracing")
        return None

    config = LangfuseConfig(
        public_key=public_key,
        secret_key=secret_key,
        host=base_url,
    )
    return LangfuseTracer(config=config, tags=["tutorial", "langfuse-demo"])


async def main():
    from hush.core import Hush

    tracer = _make_tracer()
    engine = Hush(
        build_text_analyzer(),
        env=ENV_FILE,
        resources=RESOURCES_FILE,
        **({} if tracer is None else {"tracer": tracer}),
    )

    print("=" * 50)
    print("1. LangfuseTracer — single run")
    print("=" * 50)

    result = await engine.run(
        inputs={"text": "Langfuse giup theo doi AI workflows trong production"},
        user_id="tutorial-user",
        session_id="tutorial-session",
    )
    print(f"  Word count: {result['word_count']}")
    print(f"  Category:   {result['category']}")
    print("  Trace sent to Langfuse! Check dashboard.")

    print()
    print("=" * 50)
    print("2. LangfuseTracer — multiple users")
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
