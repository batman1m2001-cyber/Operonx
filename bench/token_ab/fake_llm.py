"""The product's model in the token A/B (ENGINEERING_ENV_PLAN E7): an OpenAI-compatible fake, no cost.

A classify call (the ticket is the last user message) answers a category by keywords; a call whose
messages ask for a summary answers ``SUMMARY: …``. ``POST /_mode {"flaky": true}`` makes the first try
of every distinct request answer 503 (the planted bug of T5a/T5b); ``GET /_calls`` counts the calls.
For gate 2 (COST_PLAN E7b): ``{"delay_ms": N}`` makes every answer wait N ms (T7), ``{"fail_summary_after":
N}`` answers 503 to every summary call after the first N (T9), and a ``"stream": true`` call streams the
text one word per chunk and is counted under ``streamed`` (T8). ``{"down": true}`` answers 503 to every
call (the hard probes, COST_PLAN round 3). The growth tasks T10–T17 add three kinds, each by its word
in the messages after a summary's: ``draft`` → ``DRAFT: …``, ``escalat`` → ``ESCALATION: …``, ``urgency``
→ ``high`` when the ticket says urgent / asap / immediately / outage, else ``low``.

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
URGENT = ("urgent", "asap", "immediately", "outage")
CALLS = {"classify": 0, "summary": 0, "failed": 0, "streamed": 0, "draft": 0, "escalation": 0, "urgency": 0}
STATE = {"down": False, "flaky": False, "seen": set(), "delay_ms": 0, "fail_summary_after": None, "calls": dict(CALLS)}
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
    # the growth tasks (COST_PLAN round 3), each asked for by its word
    if any("draft" in t.lower() for t in texts):
        return "draft", "DRAFT: Thank you for writing to us. We are looking into it and will reply soon."
    if any("escalat" in t.lower() for t in texts):
        return "escalation", "ESCALATION: this ticket waited too long and needs a senior agent."
    if any("urgency" in t.lower() for t in texts):
        return "urgency", "high" if any(w in last for w in URGENT) else "low"
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
                STATE["down"] = bool(body.get("down"))
                STATE["seen"].clear()
                STATE["delay_ms"] = int(body.get("delay_ms") or 0)
                STATE["fail_summary_after"] = body.get("fail_summary_after")
                if body.get("reset"):
                    STATE["calls"] = dict(CALLS)
            return self._json(200, {"flaky": STATE["flaky"]})
        if not self.path.rstrip("/").endswith("/chat/completions"):
            return self._json(404, {"error": {"message": "only /chat/completions"}})
        messages = body.get("messages") or []
        key = hashlib.sha1(json.dumps(messages, sort_keys=True).encode()).hexdigest()
        with LOCK:
            if STATE["down"] or STATE["flaky"] and key not in STATE["seen"]:
                STATE["seen"].add(key)
                STATE["calls"]["failed"] += 1
                return self._json(503, {"error": {"message": "overloaded, try again", "type": "overloaded"}})
            kind, text = answer(messages)
            after = STATE["fail_summary_after"]
            if kind == "summary" and after is not None and STATE["calls"]["summary"] >= int(after):
                STATE["calls"]["failed"] += 1
                return self._json(503, {"error": {"message": "overloaded, try again", "type": "overloaded"}})
            STATE["calls"][kind] += 1
            if body.get("stream"):
                STATE["calls"]["streamed"] += 1
            delay = STATE["delay_ms"] / 1000
        time.sleep(delay)
        model = body.get("model") or "fake"
        if body.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            words = text.split(" ")
            pieces = [{"role": "assistant", "content": words[0]}] + [{"content": " " + w} for w in words[1:]]
            for chunk in pieces + [{}]:
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
