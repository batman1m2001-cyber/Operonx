"""A local OpenAI-compatible stand-in, so a real ``LLMOp`` runs with no key.

Deterministic: a request that offers ``tools`` gets one tool call per
tool named in the last user message (``"lookup order 42"`` calls
``lookup`` with ``{"order_id": "42"}``); any other request gets
``"Echo: <last user message>"``. Every answer reports usage, so the op
prices it when the resource names a known model. Used by the TraceView
and rescore tests::

    with fake_llm() as base_url:
        hub = llm_hub(tmp_path, base_url)
"""

from __future__ import annotations

import json
import re
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Iterator

USAGE = {"prompt_tokens": 12, "completion_tokens": 4, "total_tokens": 16}


def _tool_calls(body: dict) -> list:
    names = [t.get("function", {}).get("name") for t in body.get("tools") or []]
    last = next((m for m in reversed(body.get("messages") or []) if m.get("role") == "user"), {})
    text = str(last.get("content") or "")
    calls = []
    for i, name in enumerate(n for n in names if n and n in text):
        number = re.search(r"\d+", text)
        args = {"order_id": number.group(0)} if number else {}
        calls.append(
            {
                "id": f"call_{i}",
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(args)},
            }
        )
    return calls


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # quiet
        pass

    def do_POST(self):  # noqa: N802 — http.server's naming
        body = json.loads(self.rfile.read(int(self.headers.get("content-length") or 0)) or b"{}")
        calls = _tool_calls(body)
        last = next(
            (m for m in reversed(body.get("messages") or []) if m.get("role") == "user"), {}
        )
        message = {"role": "assistant", "content": None if calls else f"Echo: {last.get('content', '')}"}
        if calls:
            message["tool_calls"] = calls
        reply = {
            "id": "c",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": body.get("model") or "stand-in",
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "tool_calls" if calls else "stop",
                    "message": message,
                }
            ],
            "usage": USAGE,
        }
        raw = json.dumps(reply).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


@contextmanager
def fake_llm() -> Iterator[str]:
    """The stand-in's ``base_url`` while the block runs."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/v1"
    finally:
        server.shutdown()
        server.server_close()


#: The stand-in's prices: 12 prompt and 4 completion tokens cost 2e-05 USD.
PRICE_IN, PRICE_OUT = 1e-06, 2e-06


def llm_hub(tmp_path: Path, base_url: str, name: str = "bot", model: str = "stand-in") -> None:
    """Install a resource hub whose ``llm:<name>`` is the stand-in, priced."""
    from operonx.core.registry import ResourceHub

    path = tmp_path / "resources.yaml"
    path.write_text(
        f"llm:{name}:\n  api_type: openai\n  api_key: sk-local-test\n"
        f"  base_url: {base_url}\n  model: {model}\n"
        f"  cost_per_input_token: {PRICE_IN}\n  cost_per_output_token: {PRICE_OUT}\n",
        encoding="utf-8",
    )
    ResourceHub.set_instance(ResourceHub.from_yaml(path))
