"""A local OpenAI-compatible stand-in for the guide's model snippets.

The guide shows the real ``resources.yaml`` shape (``api_type: openai``,
``base_url``); the test points ``base_url`` here through ``LLM_BASE_URL``.
Its answers are deterministic: a judge prompt gets a verdict (``PASS``
when the output says ``refund``; a pairwise one, ``TIE``), a prompt asking
for an ``<intent>`` gets one, anything else is echoed back, and
``stream=true`` is honoured. Offered ``tools``, it calls the one whose
name's words the user's message holds (``order_status``: "status" and
"order"), its arguments read off the message — an ``A1``-style id for a
string, the first number for a number — and answers a tool's result with
``Done: <result>``.
"""

from __future__ import annotations

import json
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest


def _judge(messages: list):
    """A judge prompt (it asks for a JSON ``verdict``): a pairwise one is a
    tie; a binary one passes when the output under ``Output:`` says
    ``refund``."""
    system = " ".join(str(m.get("content") or "") for m in messages if m.get("role") == "system")
    if '"verdict"' not in system:
        return None
    if "A|B|TIE" in system:
        return json.dumps({"reason": "both answers say the same", "verdict": "TIE"})
    user = " ".join(str(m.get("content") or "") for m in messages if m.get("role") == "user")
    output = user.split("Output:", 1)[-1].split("Expected", 1)[0]
    verdict = "PASS" if "refund" in output else "FAIL"
    return json.dumps({"reason": f"the output says {output.strip()[:40]}", "verdict": verdict})


def _answer(messages: list) -> str:
    judged = _judge(messages)
    if judged is not None:
        return judged
    text = " ".join(str(m.get("content") or "") for m in messages)
    if "<intent>" in text or "intent" in text.lower():
        word = "refund" if "money back" in text.lower() or "refund" in text.lower() else "other"
        return f"<intent>{word}</intent>"
    last = next((m for m in reversed(messages) if m.get("role") == "user"), {})
    return f"Echo: {last.get('content', '')}"


def _tool_call(body: dict):
    """The call a request offering ``tools`` gets, or ``None``."""
    messages = body.get("messages") or []
    if not body.get("tools") or not messages or messages[-1].get("role") != "user":
        return None
    text = str(messages[-1].get("content") or "")
    words = set(re.findall(r"[a-z]+", text.lower()))
    for spec in body["tools"]:
        fn = spec.get("function") or {}
        if not set(fn.get("name", "").lower().split("_")) <= words:
            continue
        args = {}
        for name, prop in ((fn.get("parameters") or {}).get("properties") or {}).items():
            if prop.get("type") in ("integer", "number"):
                found = re.search(r"\b\d+\b", text)
                args[name] = int(found.group()) if found else 0
            else:
                found = re.search(r"\b[A-Z]\d+\b", text)
                args[name] = found.group() if found else text
        return {
            "id": "call_0",
            "type": "function",
            "function": {"name": fn["name"], "arguments": json.dumps(args)},
        }
    return None


def _reply(body: dict):
    """``(content, tool_call or None)`` for one request."""
    messages = body.get("messages") or []
    if messages and messages[-1].get("role") == "tool":
        return f"Done: {messages[-1].get('content')}", None
    call = _tool_call(body)
    if call is not None:
        return "", call
    return _answer(messages), None


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # quiet
        pass

    def do_POST(self):  # noqa: N802 — http.server's naming
        body = json.loads(self.rfile.read(int(self.headers.get("content-length") or 0)) or b"{}")
        content, call = _reply(body)
        finish = "tool_calls" if call else "stop"
        model = body.get("model") or "stand-in"
        usage = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
        if body.get("stream"):
            self.send_response(200)
            self.send_header("content-type", "text/event-stream")
            self.end_headers()
            words = content.split(" ") if content else []
            deltas = [
                {"content": word + (" " if i < len(words) - 1 else "")}
                for i, word in enumerate(words)
            ]
            if call:
                deltas.append({"tool_calls": [{"index": 0, **call}]})
            for delta in deltas:
                chunk = {
                    "id": "c",
                    "object": "chat.completion.chunk",
                    "created": int(time.time()),
                    "model": model,
                    "choices": [{"index": 0, "delta": delta, "finish_reason": None}],
                }
                self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
            end = {
                "id": "c",
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": model,
                "choices": [{"index": 0, "delta": {}, "finish_reason": finish}],
                "usage": usage,
            }
            self.wfile.write(f"data: {json.dumps(end)}\n\ndata: [DONE]\n\n".encode())
            return
        reply = {
            "id": "c",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "finish_reason": finish,
                    "message": {
                        "role": "assistant",
                        "content": content,
                        **({"tool_calls": [call]} if call else {}),
                    },
                }
            ],
            "usage": usage,
        }
        raw = json.dumps(reply).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


@pytest.fixture(scope="session")
def fake_llm():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}/v1"
    server.shutdown()
