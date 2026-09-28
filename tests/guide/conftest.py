"""A local OpenAI-compatible stand-in for the guide's model snippets.

The guide shows the real ``resources.yaml`` shape (``api_type: openai``,
``base_url``); the test points ``base_url`` here through ``LLM_BASE_URL``.
Its answers are deterministic: a prompt asking for an ``<intent>`` gets
one, anything else is echoed back, and ``stream=true`` is honoured.
"""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest


def _answer(messages: list) -> str:
    text = " ".join(str(m.get("content") or "") for m in messages)
    if "<intent>" in text or "intent" in text.lower():
        word = "refund" if "money back" in text.lower() or "refund" in text.lower() else "other"
        return f"<intent>{word}</intent>"
    last = next((m for m in reversed(messages) if m.get("role") == "user"), {})
    return f"Echo: {last.get('content', '')}"


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # quiet
        pass

    def do_POST(self):  # noqa: N802 — http.server's naming
        body = json.loads(self.rfile.read(int(self.headers.get("content-length") or 0)) or b"{}")
        content = _answer(body.get("messages") or [])
        model = body.get("model") or "stand-in"
        usage = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
        if body.get("stream"):
            self.send_response(200)
            self.send_header("content-type", "text/event-stream")
            self.end_headers()
            words = content.split(" ")
            for i, word in enumerate(words):
                delta = {"content": word + (" " if i < len(words) - 1 else "")}
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
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
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
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": content},
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
