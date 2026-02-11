"""Tutorial 10: Error Handling — Xử lý lỗi trong workflows.

Không cần API key cho ví dụ 1-3.
Ví dụ 4 cần OPENAI_API_KEY (LLM fallback).

Học được:
- Error capture trong state (workflow không crash)
- Try/catch pattern trong CodeNode
- Error output key pattern
- if_() routing dựa trên success/error
- Graceful degradation với fallback value
- Retry với exponential backoff

Chạy: cd hush-tutorial && uv run python examples/10_error_handling.py
"""

import asyncio
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent.parent / ".env")

from hush.core import END, PARENT, START, GraphNode, Hush
from hush.core.nodes import code_node, if_

# =============================================================================
# Ví dụ 1: Error capture trong state
# =============================================================================


async def example_1_error_capture():
    """Khi node lỗi, error được lưu vào state thay vì crash workflow."""
    print("=" * 50)
    print("Ví dụ 1: Error capture trong state")
    print("=" * 50)

    @code_node
    def failing():
        return {"result": 1 / 0}  # ZeroDivisionError!

    with GraphNode(name="error-demo") as graph:
        f = failing()
        START >> f >> END

    engine = Hush(graph)
    result = await engine.run(inputs={})

    # Workflow không crash — error nằm trong $state
    state = result["$state"]
    error = state["error-demo.failing", "error", None]
    print(f"  Error captured: {error is not None}")
    print("  Error type: ZeroDivisionError" if error else "  No error")


# =============================================================================
# Ví dụ 2: Try/catch pattern và error routing
# =============================================================================


@code_node
def safe_divide(a: int, b: int):
    """Chia an toàn — trả success/error thay vì throw."""
    try:
        result = a / b
        return {"success": True, "result": result, "error": None}
    except ZeroDivisionError:
        return {"success": False, "result": None, "error": "Cannot divide by zero"}


@code_node
def handle_success(result: float):
    """Xử lý kết quả thành công."""
    return {"output": f"Result: {result}"}


@code_node
def handle_error(error: str):
    """Xử lý lỗi."""
    return {"output": f"Error occurred: {error}"}


async def example_2_branch_error_routing():
    """Dùng if_() để route success/error theo nhánh khác nhau."""
    print()
    print("=" * 50)
    print("Ví dụ 2: Error routing với if_()")
    print("=" * 50)

    with GraphNode(name="error-routing") as graph:
        divide = safe_divide(
            name="divide",
            inputs={"a": PARENT["a"], "b": PARENT["b"]},
        )

        router = if_(divide["success"], "on_success").else_("on_error")

        on_success = handle_success(result = divide["result"])

        on_error = handle_error(error=divide["error"])

        START >> divide >> router >> [on_success, on_error] >> END

    engine = Hush(graph)

    # Test case 1: success
    result = await engine.run(inputs={"a": 10, "b": 3})
    print(f"  10 / 3 → {result['output']}")

    # Test case 2: error
    result = await engine.run(inputs={"a": 10, "b": 0})
    print(f"  10 / 0 → {result['output']}")


# =============================================================================
# Ví dụ 3: Graceful degradation + retry
# =============================================================================

# Simulated unreliable API
_call_count = 0


@code_node
def unreliable_api(query: str):
    """API giả lập — fail 2 lần đầu, thành công lần thứ 3."""
    global _call_count
    _call_count += 1
    if _call_count % 3 != 0:
        raise ConnectionError(f"API timeout (attempt {_call_count})")
    return {"answer": f"Result for: {query}"}


@code_node
def retry_with_backoff(query: str):
    """Retry với exponential backoff."""
    import time

    max_attempts = 3
    base_delay = 0.1  # 100ms for demo

    for attempt in range(max_attempts):
        try:
            global _call_count
            _call_count += 1
            if _call_count % 3 != 0:
                raise ConnectionError(f"Timeout (attempt {attempt + 1})")
            return {
                "success": True,
                "answer": f"Result for: {query}",
                "attempts": attempt + 1,
            }
        except ConnectionError:
            if attempt < max_attempts - 1:
                delay = base_delay * (2**attempt)
                time.sleep(delay)

    return {
        "success": False,
        "answer": "Service unavailable (fallback)",
        "attempts": max_attempts,
    }


@code_node
def with_fallback(primary_result: str, success: bool):
    """Dùng kết quả hoặc fallback."""
    if success:
        return {"output": primary_result, "used_fallback": False}
    return {"output": "Default answer (fallback)", "used_fallback": True}


async def example_3_retry_and_fallback():
    """Retry + graceful degradation khi API không ổn định."""
    print()
    print("=" * 50)
    print("Ví dụ 3: Retry + Graceful degradation")
    print("=" * 50)

    global _call_count
    _call_count = 0

    with GraphNode(name="retry-demo") as graph:
        api_call = retry_with_backoff(query = PARENT["query"])

        fallback = with_fallback(
            primary_result = api_call["answer"],
            success = api_call["success"]
        )

        START >> api_call >> fallback >> END

    engine = Hush(graph)

    # Run twice to show both paths
    for query in ["What is AI?", "What is ML?"]:
        _call_count = 0  # reset
        result = await engine.run(inputs={"query": query})
        print(f"  Query: {query}")
        print(f"    Output: {result['output']}")
        print(f"    Used fallback: {result['used_fallback']}")


# =============================================================================
# Ví dụ 4: LLM Fallback chain
# =============================================================================


async def example_4_llm_fallback():
    """LLMNode fallback — tự động chuyển model khi primary fails."""
    print()
    print("=" * 50)
    print("Ví dụ 4: LLM Fallback chain")
    print("=" * 50)

    import os

    if not os.environ.get("OPENAI_API_KEY"):
        print("  Skipped — OPENAI_API_KEY chưa set")
        print()
        print("  Cách dùng LLM fallback:")
        print("  ```python")
        print('  llm = llm_(')
        print('      resource_key="gpt-4o",')
        print('      fallback=["gpt-4o-mini"],')
        print('      messages=p["messages"],')
        print("  )")
        print("  ```")
        return

    from hush.providers import llm_, prompt_

    with GraphNode(name="llm-fallback") as graph:
        p = prompt_(
            template={
                "system": "Answer briefly.",
                "user": "{query}",
            },
            query=PARENT["query"],
        )

        # fallback: nếu gpt-4o fails → thử gpt-4o-mini
        llm = llm_(
            resource_key="gpt-4o",
            fallback=["gpt-4o-mini"],
            messages=p["messages"],
            outputs={"content": PARENT["answer"], "model_used": PARENT["model"]},
        )

        START >> p >> llm >> END

    engine = Hush(graph)
    result = await engine.run(inputs={"query": "What is Python?"})
    print(f"  Answer: {result['answer'][:80]}...")
    print(f"  Model used: {result['model']}")


# =============================================================================
# Main
# =============================================================================


async def main():
    await example_1_error_capture()
    await example_2_branch_error_routing()
    await example_3_retry_and_fallback()
    await example_4_llm_fallback()


if __name__ == "__main__":
    asyncio.run(main())
