"""The product's model in the token A/B (ENGINEERING_ENV_PLAN E7): an OpenAI-compatible fake, no cost.

A classify call (the ticket is the last user message) answers a category by keywords; a call whose
messages ask for a summary answers ``SUMMARY: …``. ``POST /_mode {"flaky": true}`` makes the first try
of every distinct request answer 503 (the planted bug of T5a/T5b); ``GET /_calls`` counts the calls.

    python fake_llm.py PORT
"""

from __future__ import annotations

import hashlib
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

KEYWORDS = (("billing", ("refund", "invoice", "charge", "payment")),
            ("account", ("password", "login", "log in", "sign in")),
            ("technical", ("crash", "error", "bug", "broken", "slow")))
STATE = {"flaky": False, "seen": set(), "calls": {"classify": 0, "summary": 0, "failed": 0}}
LOCK = threading.Lock()


def _text(content) -> str:
    if isinstance(content, list):  # [{"type": "text", "text": ...}]
        return " ".join(str(p.get("text", "")) for p in content if isinstance(p, dict))
    return str(content or "")


def answer(messages) -> tuple:
    texts = [_text(m.get("content")) for m in messages if isinstance(m, dict)]
    if any("summar" in t.lower() for t in texts):
        tickets = sum(t.count("\n") + 1 for t in texts[1:]) if len(texts) > 1 else 1
        return "summary", f"SUMMARY: {min(tickets, 99)} line(s) of tickets read."
    users = [_text(m.get("content")) for m in messages if isinstance(m, dict) and m.get("role") == "user"]
    last = (users[-1] if users else "").lower()
    cat = next((c for c, words in KEYWORDS if any(w in last for w in words)), "other")
    return "classify", cat


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # quiet
        pass

    def _json(self, code: int, body) -> None:
        raw = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if self.path.startswith("/_calls"):
            with LOCK:
                return self._json(200, dict(STATE["calls"]))
        return self._json(404, {"error": "not found"})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        if self.path.startswith("/_mode"):
            with LOCK:
                STATE["flaky"] = bool(body.get("flaky"))
                STATE["seen"].clear()
                if body.get("reset"):
                    STATE["calls"] = {"classify": 0, "summary": 0, "failed": 0}
            return self._json(200, {"flaky": STATE["flaky"]})
        if not self.path.rstrip("/").endswith("/chat/completions"):
            return self._json(404, {"error": {"message": "only /chat/completions"}})
        messages = body.get("messages") or []
        key = hashlib.sha1(json.dumps(messages, sort_keys=True).encode()).hexdigest()
        with LOCK:
            if STATE["flaky"] and key not in STATE["seen"]:
                STATE["seen"].add(key)
                STATE["calls"]["failed"] += 1
                return self._json(503, {"error": {"message": "overloaded, try again", "type": "overloaded"}})
            kind, text = answer(messages)
            STATE["calls"][kind] += 1
        model = body.get("model") or "fake"
        if body.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for chunk in ({"role": "assistant", "content": text}, {}):
                ev = {"id": "c1", "object": "chat.completion.chunk", "created": int(time.time()), "model": model,
                      "choices": [{"index": 0, "delta": chunk, "finish_reason": None if chunk else "stop"}]}
                self.wfile.write(f"data: {json.dumps(ev)}\n\n".encode())
            self.wfile.write(b"data: [DONE]\n\n")
            return None
        return self._json(200, {
            "id": "c1", "object": "chat.completion", "created": int(time.time()), "model": model,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12}})


def serve(port: int) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


if __name__ == "__main__":
    serve(int(sys.argv[1]))
    threading.Event().wait()
