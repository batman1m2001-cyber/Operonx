"""Tutorial 09: OpenTelemetry Tracing — Gửi traces qua OTEL protocol.

Cần: LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY, LANGFUSE_HOST trong .env
     (hoặc bất kỳ OTEL-compatible backend nào)

Học được:
- OTELTracer: gửi traces qua OpenTelemetry protocol
- Cấu hình OTEL endpoint cho Langfuse (hoặc Jaeger, Grafana Tempo, etc.)
- So sánh OTELTracer vs LangfuseTracer (cùng workflow, khác tracer)
- Raw OTEL SDK: gửi spans trực tiếp không qua Hush

Chạy: cd hush-tutorial && uv run python examples/09_otel_tracing.py
"""

import asyncio
import base64
import os
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent.parent / ".env")

from hush.core import Hush, GraphNode, START, END, PARENT
from hush.core.nodes.iteration.for_loop_node import ForLoopNode
from hush.core.nodes.iteration.while_loop_node import WhileLoopNode
from hush.core.nodes.iteration.base import Each
from hush.core.nodes.transform.code_node import code_node
from hush.core.tracers import BaseTracer


# =============================================================================
# Helper: tạo OTELConfig trỏ đến Langfuse
# =============================================================================

def create_langfuse_otel_config(service_name: str = "hush-tutorial"):
    """Tạo OTELConfig gửi traces đến Langfuse qua OTEL endpoint.

    Langfuse hỗ trợ nhận traces qua OTEL protocol, nên bạn không cần
    Langfuse SDK — chỉ cần standard OTEL.
    """
    from hush.observability.backends.otel import OTELConfig

    public_key = os.environ.get("LANGFUSE_PUBLIC_KEY", "")
    secret_key = os.environ.get("LANGFUSE_SECRET_KEY", "")
    host = os.environ.get("LANGFUSE_HOST", "https://cloud.langfuse.com")

    auth = base64.b64encode(f"{public_key}:{secret_key}".encode()).decode()
    return OTELConfig(
        endpoint=f"{host}/api/public/otel/v1/traces",
        protocol="http",
        headers={"Authorization": f"Basic {auth}"},
        service_name=service_name,
    )


# =============================================================================
# Code nodes
# =============================================================================

@code_node
def validate(x: int):
    """Validate input."""
    return {"validated_x": x, "$tags": ["validated"]}


@code_node
def multiply(x: int, y: int):
    """Nhân hai số."""
    product = x * y
    tags = ["multiplied"]
    if product > 50:
        tags.append("large-product")
    return {"product": product, "$tags": tags}


@code_node
def summarize(products: list):
    """Tổng hợp kết quả."""
    return {"total": sum(products) if products else 0}


@code_node
def halve_value(value: int):
    """Chia đôi giá trị (cho while loop demo)."""
    new_val = value // 2
    tags = []
    if new_val < 10:
        tags.append("small-value")
    return {"new_value": new_val, "$tags": tags} if tags else {"new_value": new_val}


# =============================================================================
# Ví dụ 1: OTELTracer với nested ForLoop
# =============================================================================

async def example_1_otel_basic():
    """OTELTracer gửi traces qua OTEL protocol đến Langfuse."""
    print("=" * 50)
    print("Ví dụ 1: OTELTracer — Nested ForLoop")
    print("=" * 50)

    if not os.environ.get("LANGFUSE_PUBLIC_KEY"):
        print("  Skipped — LANGFUSE keys chưa set trong .env")
        return

    from hush.observability import OTELTracer

    with GraphNode(name="nested-loop") as graph:
        with ForLoopNode(
            name="outer_loop",
            inputs={"x": Each([2, 3, 4])}
        ) as outer:
            val = validate(
                name="validate",
                inputs={"x": PARENT["x"]},
            )
            with ForLoopNode(
                name="inner_loop",
                inputs={"y": Each([10, 20]), "x": val["validated_x"]}
            ) as inner:
                mult = multiply(
                    name="multiply",
                    inputs={"x": PARENT["x"], "y": PARENT["y"]},
                    outputs={"*": PARENT},
                )
                START >> mult >> END

            summ = summarize(
                name="summarize",
                inputs={"products": inner["product"]},
                outputs={"*": PARENT},
            )
            START >> val >> inner >> summ >> END

        outer["total"] >> PARENT["results"]
        START >> outer >> END

    tracer = OTELTracer(
        config=create_langfuse_otel_config(),
        tags=["tutorial", "otel", "nested-loop"],
    )

    engine = Hush(graph)
    result = await engine.run(
        inputs={},
        tracer=tracer,
        user_id="alice",
        session_id="tutorial-otel",
        request_id="tutorial-otel-nested",
    )

    print(f"  Results: {result['results']}")
    state = result["$state"]
    print(f"  Tags: {state.tags}")


# =============================================================================
# Ví dụ 2: OTELTracer với WhileLoop
# =============================================================================

async def example_2_otel_while():
    """WhileLoop workflow traced qua OTEL."""
    print()
    print("=" * 50)
    print("Ví dụ 2: OTELTracer — WhileLoop")
    print("=" * 50)

    if not os.environ.get("LANGFUSE_PUBLIC_KEY"):
        print("  Skipped — LANGFUSE keys chưa set trong .env")
        return

    from hush.observability import OTELTracer

    with GraphNode(name="while-loop") as graph:
        with WhileLoopNode(
            name="halve_loop",
            inputs={"value": PARENT["start_value"]},
            stop_condition="value < 5",
            max_iterations=10,
        ) as while_loop:
            halve = halve_value(
                name="halve",
                inputs={"value": PARENT["value"]},
            )
            halve["new_value"] >> PARENT["value"]
            START >> halve >> END

        while_loop["value"] >> PARENT["final_value"]
        START >> while_loop >> END

    tracer = OTELTracer(
        config=create_langfuse_otel_config(),
        tags=["tutorial", "otel", "while-loop"],
    )

    engine = Hush(graph)
    result = await engine.run(
        inputs={"start_value": 256},
        tracer=tracer,
        user_id="bob",
        session_id="tutorial-otel",
        request_id="tutorial-otel-while",
    )

    print(f"  256 → {result['final_value']}")


# =============================================================================
# Ví dụ 3: Raw OTEL SDK (không dùng Hush)
# =============================================================================

def example_3_raw_otel():
    """Gửi spans trực tiếp bằng OTEL SDK — không cần Hush.

    Hữu ích khi bạn muốn trace code bên ngoài workflow,
    hoặc tích hợp với hệ thống OTEL có sẵn.
    """
    print()
    print("=" * 50)
    print("Ví dụ 3: Raw OTEL SDK (không dùng Hush)")
    print("=" * 50)

    if not os.environ.get("LANGFUSE_PUBLIC_KEY"):
        print("  Skipped — LANGFUSE keys chưa set trong .env")
        return

    import time
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

    public_key = os.environ.get("LANGFUSE_PUBLIC_KEY", "")
    secret_key = os.environ.get("LANGFUSE_SECRET_KEY", "")
    host = os.environ.get("LANGFUSE_HOST", "https://cloud.langfuse.com")
    auth = base64.b64encode(f"{public_key}:{secret_key}".encode()).decode()

    exporter = OTLPSpanExporter(
        endpoint=f"{host}/api/public/otel/v1/traces",
        headers={"Authorization": f"Basic {auth}"},
    )
    provider = TracerProvider()
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)

    tracer = trace.get_tracer("hush-tutorial-raw-otel")

    # Tạo trace với parent + child spans
    with tracer.start_as_current_span("my-pipeline") as parent:
        parent.set_attribute("user_id", "tutorial-user")

        with tracer.start_as_current_span("fetch-data") as span:
            span.set_attribute("source", "database")
            time.sleep(0.05)
            span.set_attribute("records", 42)

        with tracer.start_as_current_span("process") as span:
            span.set_attribute("algorithm", "transform-v2")
            time.sleep(0.03)

            with tracer.start_as_current_span("validate") as sub:
                sub.set_attribute("passed", True)
                time.sleep(0.01)

        with tracer.start_as_current_span("llm-call") as span:
            span.set_attribute("gen_ai.system", "openai")
            span.set_attribute("gen_ai.request.model", "gpt-4")
            span.set_attribute("gen_ai.usage.prompt_tokens", 150)
            span.set_attribute("gen_ai.usage.completion_tokens", 50)
            time.sleep(0.05)

    provider.force_flush()
    print("  Sent raw OTEL spans to Langfuse!")
    print("  → Check Langfuse UI for 'my-pipeline' trace")


# =============================================================================
# Main
# =============================================================================

async def main():
    await example_1_otel_basic()
    await example_2_otel_while()
    example_3_raw_otel()

    print()
    print("Flushing traces...")
    BaseTracer.shutdown_executor()
    print("Done!")


if __name__ == "__main__":
    asyncio.run(main())
