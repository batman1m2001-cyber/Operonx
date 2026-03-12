"""06 Tracing / OTEL — Run with OTELTracer.

Exports traces to OTLP endpoint (default: localhost:4317 gRPC / Jaeger).
Needs: pip install hush-telemetry[otel]

Chạy: cd examples && uv run python 06_tracing/otel/demo.py
"""

import asyncio
import os

from workflow import build_text_analyzer


def _make_tracer():
    """Create OTELTracer with console or Jaeger exporter."""
    try:
        from hush.telemetry import OTELTracer
        from hush.telemetry.backends.otel import OTELConfig
    except ImportError:
        import warnings

        warnings.warn("hush-telemetry[otel] not installed — running without OTEL tracing")
        return None

    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")
    config = OTELConfig(
        endpoint=endpoint,
        protocol="grpc",
        service_name="hush-tutorial",
        insecure=True,
    )
    return OTELTracer(config=config, tags=["tutorial", "otel-demo"])


async def main():
    from hush.core import Hush

    tracer = _make_tracer()
    engine = Hush(build_text_analyzer(), **({} if tracer is None else {"tracer": tracer}))

    print("=" * 50)
    print("1. OTELTracer — single run")
    print("=" * 50)

    result = await engine.run(
        inputs={"text": "OpenTelemetry giup export traces den bat ky backend nao"},
        user_id="tutorial-user",
        session_id="tutorial-session",
    )
    print(f"  Word count: {result['word_count']}")
    print(f"  Category:   {result['category']}")
    print("  Trace exported via OTLP!")

    print()
    print("=" * 50)
    print("2. OTELTracer — multiple users")
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
