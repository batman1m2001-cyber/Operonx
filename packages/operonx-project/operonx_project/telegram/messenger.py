"""The Bot API, as the bridge needs it: send, edit, answer a button, long-poll updates.

The token is part of every Bot API URL, so this client never logs a URL:
it uses the standard library (``urllib``, which logs nothing), names only
the method in its own log lines and errors, and its ``repr`` hides the
token. A 429 or 5xx is :class:`TelegramError` with ``transient=True``
(the caller retries it); anything else 4xx is permanent.
"""

from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Protocol, Sequence, Tuple

from operonx.core.loggings import LOGGER

__all__ = ["Messenger", "TelegramError", "TelegramMessenger"]

Buttons = Sequence[Sequence[Tuple[str, str]]]


class TelegramError(RuntimeError):
    def __init__(self, method: str, code: Optional[int], description: str, retry_after: Optional[float] = None):
        super().__init__(f"telegram {method}: {code} {description}")
        self.code = code
        self.retry_after = retry_after
        self.transient = code is None or code == 429 or code >= 500


class Messenger(Protocol):
    async def send(self, chat: Any, text: str, buttons: Buttons = (), reply_to: Optional[int] = None) -> int: ...

    async def edit(self, chat: Any, message_id: int, text: str, buttons: Buttons = ()) -> None: ...

    async def answer_callback(self, callback_id: str, text: str = "") -> None: ...

    async def updates(self, offset: int, timeout: float) -> List[Dict[str, Any]]: ...


def _markup(buttons: Buttons) -> Optional[Dict[str, Any]]:
    rows = [[{"text": t, "callback_data": d} for t, d in row] for row in buttons if row]
    return {"inline_keyboard": rows} if rows else None


class TelegramMessenger:
    def __init__(self, token: str, api: str = "https://api.telegram.org", timeout: float = 30.0):
        if not token or ":" not in token:
            raise ValueError("a Telegram bot token looks like 123456:ABC…")
        self._token = token
        self.api = api.rstrip("/")
        self.timeout = timeout

    def __repr__(self) -> str:
        return f"TelegramMessenger(api={self.api!r}, token=<hidden>)"

    def _post(self, method: str, params: Dict[str, Any], timeout: float) -> Any:
        req = urllib.request.Request(
            f"{self.api}/bot{self._token}/{method}",
            data=json.dumps({k: v for k, v in params.items() if v is not None}).encode(),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 — the API base is configured
                body = json.loads(resp.read().decode() or "{}")
        except urllib.error.HTTPError as exc:
            try:
                body = json.loads(exc.read().decode() or "{}")
            except ValueError:
                body = {}
            retry = (body.get("parameters") or {}).get("retry_after")
            raise TelegramError(method, exc.code, str(body.get("description") or exc.reason), retry) from None
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            reason = getattr(exc, "reason", exc)
            raise TelegramError(method, None, f"unreachable: {type(reason).__name__}") from None
        if not body.get("ok"):
            raise TelegramError(method, body.get("error_code"), str(body.get("description") or "not ok"))
        return body.get("result")

    async def call(self, method: str, http_timeout: Optional[float] = None, **params: Any) -> Any:
        LOGGER.debug(f"telegram {method}")
        return await asyncio.to_thread(self._post, method, params, http_timeout or self.timeout)

    async def send(self, chat: Any, text: str, buttons: Buttons = (), reply_to: Optional[int] = None) -> int:
        params: Dict[str, Any] = {"chat_id": chat, "text": text, "reply_markup": _markup(buttons)}
        if reply_to:
            params["reply_parameters"] = {"message_id": reply_to, "allow_sending_without_reply": True}
        out = await self.call("sendMessage", **params)
        return int(out["message_id"])

    async def edit(self, chat: Any, message_id: int, text: str, buttons: Buttons = ()) -> None:
        await self.call("editMessageText", chat_id=chat, message_id=message_id, text=text,
                        reply_markup=_markup(buttons) or {"inline_keyboard": []})  # fmt: skip

    async def answer_callback(self, callback_id: str, text: str = "") -> None:
        await self.call("answerCallbackQuery", callback_query_id=callback_id, text=text[:200])

    async def updates(self, offset: int, timeout: float) -> List[Dict[str, Any]]:
        got = await self.call("getUpdates", http_timeout=timeout + 10, offset=offset, timeout=int(timeout),
                              allowed_updates=["message", "callback_query"])  # fmt: skip
        return list(got or [])
