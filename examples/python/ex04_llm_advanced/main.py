"""04 LLM Advanced — structured output, tool calling, multi-turn chat.

Requires ``OPENAI_API_KEY`` in ``.env`` and ``llm:gpt-4o-mini`` in
``resources.yaml``. Run from this directory:

    uv sync
    cp .env.example .env  # fill in OPENAI_API_KEY
    uv run python main.py
"""

from __future__ import annotations

import asyncio
import json

import operonx
from operonx.core import END, START, Operon, graph, op
from operonx.providers import LLMOp

# ── Tool-calling helpers ────────────────────────────────────────────────

CALC_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "calculate",
            "description": "Tính toán biểu thức toán học",
            "parameters": {
                "type": "object",
                "properties": {
                    "expression": {
                        "type": "string",
                        "description": "Biểu thức toán (vd: 2+3*4)",
                    }
                },
                "required": ["expression"],
            },
        },
    }
]


def _execute_tool(expression: str) -> str:
    try:
        return str(eval(expression))  # demo only — production needs safer eval
    except Exception as e:
        return f"Error: {e}"


@op
def process_response(content, tool_calls):
    return {
        "has_tool_call": bool(tool_calls),
        "tool_result": (
            _execute_tool(json.loads(tool_calls[0]["function"]["arguments"])["expression"])
            if tool_calls
            else None
        ),
        "llm_response": content,
    }


@op
def build_messages(history: list, message: str) -> dict:
    """Assemble the full messages list: system + history + new user turn.

    Prompt-formatting inside LLMOp only handles `{var}` substitution — any
    history-injection lives in caller code.
    """
    return {
        "messages": [
            {"role": "system", "content": "Bạn là assistant hữu ích. Trả lời ngắn gọn."},
            *history,
            {"role": "user", "content": message},
        ]
    }


@op
def update_history(history, message, response):
    return {
        "new_history": history
        + [
            {"role": "user", "content": message},
            {"role": "assistant", "content": response},
        ]
    }


# ── Graphs ──────────────────────────────────────────────────────────────


@graph
def structured_output(text):
    """Force the LLM to return JSON matching a schema (sentiment analysis)."""
    llm = LLMOp.of(
        resource="gpt-4o-mini",
        prompt={
            "system": "Phân tích sentiment của văn bản. Trả về JSON.",
            "user": "{text}",
        },
        text=text,
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": "sentiment_response",
                "schema": {
                    "type": "object",
                    "properties": {
                        "sentiment": {
                            "type": "string",
                            "enum": ["positive", "negative", "neutral"],
                        },
                        "confidence": {"type": "number"},
                        "reason": {"type": "string"},
                    },
                    "required": ["sentiment", "confidence", "reason"],
                },
            },
        },
    )
    START >> llm >> END


@graph
def tool_calling(query):
    """LLM uses a `calculate` function tool."""
    llm = LLMOp.of(
        resource="gpt-4o-mini",
        prompt={
            "system": "Bạn có thể tính toán. Dùng tool calculate khi cần.",
            "user": "{query}",
        },
        query=query,
        tools=CALC_TOOLS,
        tool_choice="auto",
    )
    proc = process_response(content=llm["content"], tool_calls=llm["tool_calls"])
    START >> llm >> proc >> END


@graph
def multi_turn(history, message):
    """Multi-turn conversation — caller assembles history + user turn."""
    msgs = build_messages(history=history, message=message)
    llm = LLMOp.of(
        resource="gpt-4o-mini",
        # A conversation is data — `messages=` is never formatted. Passing
        # it to `prompt=` would try to resolve every brace in the history
        # as a template variable.
        messages=msgs["messages"],
        temperature=0.7,
        max_tokens=200,
    )
    upd = update_history(history=history, message=message, response=llm["content"])
    START >> msgs >> llm >> upd >> END


async def main() -> None:
    operonx.bootstrap()

    runs = [
        ("structured", structured_output(text="Sản phẩm tuyệt vời, rất hài lòng!")),
        ("tool", tool_calling(query="Tính 25 * 4 + 100")),
        ("multi_turn", multi_turn(history=[], message="Xin chào! Tên tôi là An.")),
    ]
    for label, g in runs:
        result = await Operon(g).run(inputs={})
        content = {k: v for k, v in result.items() if k != "$state"}
        print(f"[{label}] {content}")


if __name__ == "__main__":
    asyncio.run(main())
