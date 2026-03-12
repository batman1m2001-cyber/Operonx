"""Shared workflow definitions for 08_error_handling.

Defines error handling ops and graph builders.
Examples 1-3: no API keys required (pure compute).
Example 4: requires OPENAI_API_KEY (LLM fallback).
"""

from hush.core import END, PARENT, START, GraphOp
from hush.core.ops import if_, op

# =============================================================================
# Ops — Error capture
# =============================================================================


@op(rust="./rust_ops::pipeline::failing_op")
def failing():
    return {"result": 1 / 0}  # ZeroDivisionError!


# =============================================================================
# Ops — Try/catch + error routing
# =============================================================================


@op(rust="./rust_ops::math::safe_divide")
def safe_divide(a: int, b: int):
    """Chia an toàn — trả success/error thay vì throw."""
    try:
        result = a / b
        return {"success": True, "result": result, "error": None}
    except ZeroDivisionError:
        return {"success": False, "result": None, "error": "Cannot divide by zero"}


@op(rust="./rust_ops::text::handle_success")
def handle_success(result: float):
    """Xử lý kết quả thành công."""
    return {"output": f"Result: {result}"}


@op(rust="./rust_ops::text::handle_error")
def handle_error(error: str):
    """Xử lý lỗi."""
    return {"output": f"Error occurred: {error}"}


# =============================================================================
# Ops — Retry + fallback
# =============================================================================


@op(rust="./rust_ops::pipeline::retry_with_backoff")
def retry_with_backoff(query: str):
    """Retry với exponential backoff — simulates unreliable API."""
    attempt_count = 0
    max_attempts = 3

    for attempt in range(max_attempts):
        try:
            attempt_count += 1
            if attempt_count < 3:
                raise ConnectionError(f"Timeout (attempt {attempt + 1})")
            return {
                "success": True,
                "answer": f"Result for: {query}",
                "attempts": attempt + 1,
            }
        except ConnectionError:
            pass

    return {
        "success": False,
        "answer": "Service unavailable (fallback)",
        "attempts": max_attempts,
    }


@op(rust="./rust_ops::pipeline::with_fallback")
def with_fallback(primary_result: str, success: bool):
    """Dùng kết quả hoặc fallback."""
    if success:
        return {"output": primary_result, "used_fallback": False}
    return {"output": "Default answer (fallback)", "used_fallback": True}


# =============================================================================
# Graph builders
# =============================================================================


def build_error_capture():
    """Graph: op fails → error captured in state, workflow doesn't crash."""
    with GraphOp(name="error-capture") as graph:
        fail = failing()
        START >> fail >> END
    return graph


def build_error_routing():
    """Graph: safe divide → if_(success) routes to success/error handler."""
    with GraphOp(name="error-routing") as graph:
        divide = safe_divide(
            name="divide",
            inputs={"a": PARENT["a"], "b": PARENT["b"]},
        )
        router = if_(divide["success"], "on_success").else_("on_error")
        on_success = handle_success(result=divide["result"])
        on_error = handle_error(error=divide["error"])
        START >> divide >> router >> [on_success, on_error] >> END
    return graph


def build_retry_fallback():
    """Graph: retry with backoff → fallback on failure."""
    with GraphOp(name="retry-fallback") as graph:
        api_call = retry_with_backoff(query=PARENT["query"])
        fallback = with_fallback(primary_result=api_call["answer"], success=api_call["success"])
        START >> api_call >> fallback >> END
    return graph


def build_llm_fallback():
    """Graph: LLM with fallback chain (gpt-4o → gpt-4o-mini)."""
    from hush.providers import LLMOp, PromptOp

    with GraphOp(name="llm-fallback") as graph:
        p = PromptOp.of(
            template={
                "system": "Answer briefly.",
                "user": "{query}",
            },
            query=PARENT["query"],
        )
        llm = LLMOp.of(
            resource="gpt-4o",
            fallback=["gpt-4o-mini"],
            messages=p["messages"],
            outputs={"content": PARENT["answer"], "model_used": PARENT["model"]},
        )
        START >> p >> llm >> END
    return graph
