"""Shared workflow definitions for ex04_llm_advanced.

Structured output, Tool calling, Multi-turn chat graphs.

Cần: OPENAI_API_KEY trong .env
"""

import json

from operon.core import END, PARENT, START, GraphOp, op
from operon.providers import LLMOp, PromptOp

# --- Tool calling helpers ---

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
        return str(eval(expression))  # Demo only — production needs safer eval
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
def update_history(history, message, response):
    return {
        "new_history": history
        + [
            {"role": "user", "content": message},
            {"role": "assistant", "content": response},
        ]
    }


# --- Graph builders ---


def build_structured_output() -> GraphOp:
    """Force LLM trả về JSON theo schema (sentiment analysis)."""
    with GraphOp(name="sentiment-analysis") as graph:
        p = PromptOp.of(
            template={
                "system": "Phân tích sentiment của văn bản. Trả về JSON.",
                "user": "{text}",
            },
            text=PARENT["text"],
        )
        llm = LLMOp.of(
            resource="gpt-4o-mini",
            messages=p["messages"],
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
            outputs={"content": PARENT["analysis"]},
        )
        START >> p >> llm >> END
    return graph


def build_tool_calling() -> GraphOp:
    """LLM sử dụng tools (function calling)."""
    with GraphOp(name="tool-calling") as graph:
        p = PromptOp.of(
            template={
                "system": "Bạn có thể tính toán. Dùng tool calculate khi cần.",
                "user": "{query}",
            },
            query=PARENT["query"],
        )
        llm = LLMOp.of(
            resource="gpt-4o-mini",
            messages=p["messages"],
            tools=CALC_TOOLS,
            tool_choice="auto",
        )
        proc = process_response(
            content=llm["content"],
            tool_calls=llm["tool_calls"],
        )
        START >> p >> llm >> proc >> END
    return graph


def build_multi_turn() -> GraphOp:
    """Multi-turn conversation giữ history."""
    with GraphOp(name="multi-turn-chat") as graph:
        p = PromptOp.of(
            template={
                "system": "Bạn là assistant hữu ích. Trả lời ngắn gọn.",
                "user": "{message}",
            },
            conversation_history=PARENT["history"],
            message=PARENT["message"],
        )
        llm = LLMOp.of(
            resource="gpt-4o-mini",
            messages=p["messages"],
            temperature=0.7,
            max_tokens=200,
            outputs={"content": PARENT["response"]},
        )
        upd = update_history(
            history=PARENT["history"],
            message=PARENT["message"],
            response=llm["content"],
        )
        START >> p >> llm >> upd >> END
    return graph
