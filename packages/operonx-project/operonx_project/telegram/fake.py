"""``FakeBotAPI``: a **test double** of the Telegram Bot API, served over real HTTP on localhost.

It speaks the methods the bridge uses (``sendMessage``, ``editMessageText``,
``answerCallbackQuery``, ``getUpdates``) at ``/bot<token>/<method>``, checks
the token (401 otherwise), and keeps what it was sent. A test plays the
owner by pushing updates (a message, a reply, a button tap), and can make
the next sends fail (``fail_next``) to exercise retries. ``getUpdates``
long-polls like the real one, up to its ``timeout`` (capped at 2 s here).

Not Telegram: no rate limits, no formatting rules, one bot.
"""

from __future__ import annotations

import itertools
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional

__all__ = ["FakeBotAPI"]


class FakeBotAPI:
    def __init__(self, token: str = "123456:TEST-token-for-the-fake-bot-api-000000"):
        self.token = token
        self.sent: List[Dict[str, Any]] = []  # every sendMessage, with its message_id
        self.edits: List[Dict[str, Any]] = []
        self.callbacks: List[Dict[str, Any]] = []  # answerCallbackQuery calls
        self.requests: List[str] = []  # method names, in order
        self._updates: List[Dict[str, Any]] = []
        self._ids = itertools.count(1)
        self._update_ids = itertools.count(1000)
        self._fail: List[int] = []
        self._cond = threading.Condition()
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:  # no request lines on stderr (they hold the token)
                pass

            def do_POST(self) -> None:  # noqa: N802 — http.server's name
                n = int(self.headers.get("Content-Length") or 0)
                params = json.loads(self.rfile.read(n).decode() or "{}")
                code, body = fake._handle(self.path, params)
                data = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._server.server_address[1]}"

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()

    # ── the test plays the owner ───────────────────────────────────────────
    def fail_next(self, n: int, code: int = 500) -> None:
        """The next *n* ``sendMessage`` calls fail with *code*."""
        with self._cond:
            self._fail += [code] * n

    def push(self, update: Dict[str, Any]) -> int:
        """Queue a raw update (its ``update_id`` kept when given: a duplicate)."""
        with self._cond:
            update = dict(update)
            update.setdefault("update_id", next(self._update_ids))
            self._updates.append(update)
            self._cond.notify_all()
            return update["update_id"]

    def message(self, user: int, chat: int, text: str, reply_to: Optional[int] = None) -> int:
        msg: Dict[str, Any] = {"message_id": next(self._ids), "from": {"id": user}, "chat": {"id": chat},
                               "date": int(time.time()), "text": text}  # fmt: skip
        if reply_to is not None:
            msg["reply_to_message"] = {"message_id": reply_to, "chat": {"id": chat}}
        return self.push({"message": msg})

    def tap(self, user: int, chat: int, message_id: int, data: str) -> int:
        cq = {"id": f"cq{next(self._ids)}", "from": {"id": user}, "data": data,
              "message": {"message_id": message_id, "chat": {"id": chat}}}  # fmt: skip
        return self.push({"callback_query": cq})

    def last_to(self, chat: int) -> Dict[str, Any]:
        return [m for m in self.sent if str(m["chat_id"]) == str(chat)][-1]

    # ── the API ────────────────────────────────────────────────────────────
    def _handle(self, path: str, params: Dict[str, Any]):
        prefix = f"/bot{self.token}/"
        if not path.startswith(prefix):
            return 401, {"ok": False, "error_code": 401, "description": "Unauthorized"}
        method = path[len(prefix) :]
        self.requests.append(method)
        if method == "sendMessage":
            with self._cond:
                code = self._fail.pop(0) if self._fail else None
            if code:
                return code, {"ok": False, "error_code": code, "description": "fake failure"}
            mid = next(self._ids)
            buttons = [[b.get("callback_data") for b in row] for row in
                       ((params.get("reply_markup") or {}).get("inline_keyboard") or [])]  # fmt: skip
            self.sent.append({**params, "message_id": mid, "buttons": buttons})
            return 200, {"ok": True, "result": {"message_id": mid, "chat": {"id": params.get("chat_id")}}}
        if method == "editMessageText":
            self.edits.append(params)
            return 200, {"ok": True, "result": True}
        if method == "answerCallbackQuery":
            self.callbacks.append(params)
            return 200, {"ok": True, "result": True}
        if method == "getUpdates":
            offset = int(params.get("offset") or 0)
            deadline = time.time() + min(float(params.get("timeout") or 0), 2.0)
            with self._cond:
                while True:
                    got = [u for u in self._updates if u["update_id"] >= offset]
                    if got or time.time() >= deadline:
                        return 200, {"ok": True, "result": got}
                    self._cond.wait(max(0.0, deadline - time.time()))
        return 404, {"ok": False, "error_code": 404, "description": f"no method {method}"}
