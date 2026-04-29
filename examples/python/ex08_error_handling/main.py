"""08 Error Handling — capture, route, retry+fallback, LLM fallback chain.

Scenarios 1-3 are pure compute (tier 1); scenario 4 needs
``OPENAI_API_KEY`` and ``llm:gpt-4o`` + ``llm:gpt-4o-mini`` in
``resources.yaml``.

Run from this directory:

    uv sync
    cp .env.example .env  # only for the llm_fallback scenario
    uv run python main.py
"""

from __future__ import annotations

import asyncio

import operonx
from operonx.core import END, PARENT, START, Operon, graph, op
from operonx.core.ops.flow.branch_op import if_
from operonx.providers import LLMOp, PromptOp

# ── Pure-compute ops ────────────────────────────────────────────────────


@op
def failing():
    return {"result": 1 / 0}  # ZeroDivisionError


@op
def safe_divide(a: int, b: int):
    try:
        return {"success": True, "result": a / b, "error": None}
    except ZeroDivisionError:
        return {"success": False, "result": None, "error": "Cannot divide by zero"}


@op
def handle_success(result: float):
    return {"output": f"Result: {result}"}


@op
def handle_error(error: str):
    return {"output": f"Error occurred: {error}"}


@op
def retry_with_backoff(query: str):
    """Simulates an unreliable API; succeeds on third attempt."""
    max_attempts = 3
    for attempt in range(max_attempts):
        try:
            if attempt + 1 < 3:
                raise ConnectionError(f"Timeout (attempt {attempt + 1})")
            return {"success": True, "answer": f"Result for: {query}", "attempts": attempt + 1}
        except ConnectionError:
            continue
    return {"success": False, "answer": "Service unavailable (fallback)", "attempts": max_attempts}


@op
def with_fallback(primary_result: str, success: bool):
    if success:
        return {"output": primary_result, "used_fallback": False}
    return {"output": "Default answer (fallback)", "used_fallback": True}


# ── Graphs ──────────────────────────────────────────────────────────────


@graph
def error_capture():
    """Op fails → error captured in state, workflow doesn't crash."""
    fail = failing()
    START >> fail >> END


@graph
def error_routing(a, b):
    """`if_(success)` routes to success / error handler."""
    divide = safe_divide(a=a, b=b)
    router = if_(divide["success"], "on_success").else_("on_error")
    on_success = handle_success(result=divide["result"])
    on_error = handle_error(error=divide["error"])
    on_success["output"] >> PARENT["output"]
    on_error["output"] >> PARENT["output"]
    START >> divide >> router
    router >> [on_success, on_error]
    [on_success, on_error] >> ~END


@graph
def retry_fallback(query):
    """Retry with backoff → fallback on failure."""
    api_call = retry_with_backoff(query=query)
    fb = with_fallback(primary_result=api_call["answer"], success=api_call["success"])
    START >> api_call >> fb >> END


@graph
def llm_fallback(query):
    """LLM call with fallback chain — gpt-4o → gpt-4o-mini."""
    p = PromptOp.of(
        template={"system": "Answer briefly.", "user": "{query}"},
        query=query,
    )
    llm = LLMOp.of(
        resource="gpt-4o",
        fallback=["gpt-4o-mini"],
        messages=p["messages"],
    )
    START >> p >> llm >> END


async def main() -> None:
    operonx.bootstrap()

    # Scenarios 1–3: pure compute, no API.
    pure_runs = [
        ("capture", error_capture(), {}),
        ("routing", error_routing(a=PARENT["a"], b=PARENT["b"]), {"a": 10, "b": 3}),
        ("retry", retry_fallback(query=PARENT["query"]), {"query": "What is AI?"}),
    ]
    for label, g, inputs in pure_runs:
        try:
            result = await Operon(g).run(inputs=inputs)
            content = {k: v for k, v in result.items() if k != "$state"}
            print(f"[{label}] {content}")
        except Exception as e:
            print(f"[{label}] error: {e!r}")

    # Scenario 4: needs OPENAI_API_KEY.
    try:
        g = llm_fallback(query=PARENT["query"])
        result = await Operon(g).run(inputs={"query": "What is Python?"})
        content = {k: v for k, v in result.items() if k != "$state"}
        print(f"[llm_fallback] {content}")
    except Exception as e:
        print(f"[llm_fallback] skipped: {e!r}")


if __name__ == "__main__":
    asyncio.run(main())
