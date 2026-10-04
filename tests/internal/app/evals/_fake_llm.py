"""A local OpenAI-compatible stand-in, so a real ``LLMOp`` runs with no key.

Deterministic: a request that offers ``tools`` gets one tool call per
tool named in the last user message (``"lookup order 42"`` calls
``lookup`` with ``{"order_id": "42"}``); any other request gets
``"Echo: <last user message>"`` — unless the test gives an ``answer``
function, which sees each request body and returns the reply's text
(``None`` falls back to the above). Every answer reports usage, so the op
prices it when the resource names a known model; every request is kept in
the server's ``requests`` list, so a test can count the calls a run made.
Used by the TraceView, rescore and judge tests::

    with fake_llm() as server:
        hub = llm_hub(tmp_path, server.base_url)
        ...
        assert len(server.requests) == 2
"""

from __future__ import annotations

import json
import re
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable, Iterator, List, Optional

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
    #: Set per server by `fake_llm`.
    answer: Optional[Callable[[dict], Optional[str]]] = None
    delay: float = 0.0
    requests: List[dict] = []

    def log_message(self, *args):  # quiet
        pass

    def do_POST(self):  # noqa: N802 — http.server's naming
        body = json.loads(self.rfile.read(int(self.headers.get("content-length") or 0)) or b"{}")
        self.requests.append(body)
        if self.delay:
            time.sleep(self.delay)
        scripted = type(self).answer(body) if type(self).answer is not None else None
        calls = [] if scripted is not None else _tool_calls(body)
        last = next(
            (m for m in reversed(body.get("messages") or []) if m.get("role") == "user"), {}
        )
        message = {
            "role": "assistant",
            "content": scripted
            if scripted is not None
            else (None if calls else f"Echo: {last.get('content', '')}"),
        }
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


@dataclass
class FakeLLM:
    """A running stand-in: where it listens, and every request it got."""

    base_url: str
    requests: List[dict] = field(default_factory=list)


@contextmanager
def fake_llm(
    answer: Optional[Callable[[dict], Optional[str]]] = None, delay: float = 0.0
) -> Iterator[FakeLLM]:
    """The stand-in while the block runs. *answer* scripts the replies;
    *delay* (seconds) is how long each request takes."""
    got = FakeLLM("")
    handler = type(
        "Handler",
        (_Handler,),
        {
            "answer": staticmethod(answer) if answer else None,
            "delay": delay,
            "requests": got.requests,
        },
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    got.base_url = f"http://127.0.0.1:{server.server_address[1]}/v1"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield got
    finally:
        server.shutdown()
        server.server_close()


#: The stand-in's prices: 12 prompt and 4 completion tokens cost 2e-05 USD.
PRICE_IN, PRICE_OUT = 1e-06, 2e-06


def llm_hub(
    tmp_path: Path,
    base_url: str,
    name: str = "bot",
    model: str = "stand-in",
    *,
    more: Optional[dict] = None,
    key: str = "sk-local-test",
) -> None:
    """Install a resource hub whose ``llm:<name>`` is the stand-in, priced —
    and each ``llm:<other>`` of *more* (``{other: model}``) as well, all
    answered by the same stand-in."""
    from operonx.core.registry import ResourceHub

    entries = {name: model, **(more or {})}
    path = tmp_path / "resources.yaml"
    path.write_text(
        "".join(
            f"llm:{n}:\n  api_type: openai\n  api_key: {key}\n"
            f"  base_url: {base_url}\n  model: {m}\n"
            f"  cost_per_input_token: {PRICE_IN}\n  cost_per_output_token: {PRICE_OUT}\n"
            for n, m in entries.items()
        ),
        encoding="utf-8",
    )
    ResourceHub.set_instance(ResourceHub.from_yaml(path))
