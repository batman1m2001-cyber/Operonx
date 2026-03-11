"""Tutorial 09: OpenTelemetry Tracing — Gửi traces qua OTEL protocol.

Cần: LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY, LANGFUSE_HOST trong .env
     (hoặc bất kỳ OTEL-compatible backend nào)

Học được:
- OTELTracer: gửi traces qua OpenTelemetry protocol
- Cấu hình OTEL endpoint cho Langfuse (hoặc Jaeger, Grafana Tempo, etc.)
- So sánh OTELTracer vs LangfuseTracer (cùng workflow, khác tracer)
- Raw OTEL SDK: gửi spans trực tiếp không qua Hush

Chạy: uv run python examples/09_otel_tracing.py
"""

import asyncio
import base64
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent.parent / ".env")

from hush.core import END, PARENT, START, GraphOp, Hush
from hush.core.ops import op

# =============================================================================
# Helper: tạo OTELConfig trỏ đến Langfuse
# =============================================================================


def create_langfuse_otel_config(service_name: str = "tutorial"):
    """Tạo OTELConfig gửi traces đến Langfuse qua OTEL endpoint.

    Langfuse hỗ trợ nhận traces qua OTEL protocol, nên bạn không cần
    Langfuse SDK — chỉ cần standard OTEL.
    """
    from hush.telemetry.backends.otel import OTELConfig

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
# Code ops
# =============================================================================


@op(rust="./rust_ops::pipeline::validate_input")
def validate(x: int):
    """Validate input."""
    return {"validated_x": x, "$tags": ["validated"]}


@op(rust="./rust_ops::math::multiply_xy_tagged")
def multiply(x: int, y: int):
    """Nhân hai số."""
    product = x * y
    tags = ["multiplied"]
    if product > 50:
        tags.append("large-product")
    return {"product": product, "$tags": tags}


@op(rust="./rust_ops::analytics::summarize_products")
def summarize(products: list):
    """Tổng hợp kết quả."""
    return {"total": sum(products) if products else 0}


@op(rust="./rust_ops::iteration::halve_until_threshold")
def halve_until_small(value: int, threshold: int = 5):
    """Chia đôi giá trị cho đến khi nhỏ hơn threshold (thay thế WhileOp)."""
    while value >= threshold:
        value = value // 2
        tags = []
        if value < 10:
            tags.append("small-value")
        yield {"new_value": value, "$tags": tags} if tags else {"new_value": value}


# =============================================================================
# Generator ops cho iteration (thay thế ForOp + Each)
# =============================================================================


@op(rust="./rust_ops::iteration::each_x")
def each_x(xs: list):
    """Yield từng x — thay thế outer ForOp."""
    for x in xs:
        yield {"x": x}


@op(rust="./rust_ops::iteration::each_y")
def each_y(ys: list):
    """Yield từng y — thay thế inner ForOp."""
    for y in ys:
        yield {"y": y}


# =============================================================================
# Ví dụ 1: OTELTracer với nested iteration
# =============================================================================


async def example_1_otel_basic():
    """OTELTracer gửi traces qua OTEL protocol đến Langfuse."""
    print("=" * 50)
    print("Ví dụ 1: OTELTracer — Nested Iteration")
    print("=" * 50)

    if not os.environ.get("LANGFUSE_PUBLIC_KEY"):
        print("  Skipped — LANGFUSE keys chưa set trong .env")
        return

    from hush.telemetry import OTELTracer

    # Outer loop: iterate x values, validate, then inner loop multiply with y values
    with GraphOp(name="nested-loop") as graph:
        src = each_x(xs=PARENT["xs"])
        val = validate(x=src["x"])
        inner = each_y(ys=PARENT["ys"])
        mult = multiply(x=val["validated_x"], y=inner["y"])
        summ = summarize(products=mult["product"])

        summ["total"] >> PARENT["results"]

        START >> src >> val >> inner >> mult >> summ >> END

    tracer = OTELTracer(
        config=create_langfuse_otel_config(),
        tags=["tutorial", "otel", "nested-loop"],
    )

    engine = Hush(graph)
    result = await engine.run(
        inputs={"xs": [2, 3, 4], "ys": [10, 20]},
        tracer=tracer,
        user_id="alice",
        session_id="tutorial-otel",
        request_id="tutorial-otel-nested",
    )

    print(f"  Results: {result['results']}")
    state = result["$state"]
    print(f"  Tags: {state.tags}")


# =============================================================================
# Ví dụ 2: OTELTracer với While Loop (generator)
# =============================================================================


async def example_2_otel_while():
    """While loop workflow traced qua OTEL — dùng generator thay WhileOp."""
    print()
    print("=" * 50)
    print("Ví dụ 2: OTELTracer — While Loop (generator)")
    print("=" * 50)

    if not os.environ.get("LANGFUSE_PUBLIC_KEY"):
        print("  Skipped — LANGFUSE keys chưa set trong .env")
        return

    from hush.telemetry import OTELTracer

    with GraphOp(name="while-loop") as graph:
        halve = halve_until_small(value=PARENT["start_value"], threshold=5)
        halve["new_value"] >> PARENT["final_value"]
        START >> halve >> END

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
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

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

    tracer = trace.get_tracer("tutorial-raw-otel")

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


if __name__ == "__main__":
    asyncio.run(main())
