"""The bridge: interactions out as Telegram messages, the owner's words and taps back in (plan §9).

Out (:meth:`TelegramBridge.sync`, every daemon loop): each open question is
sent once, with its buttons; a send that fails is retried (transient errors
at once, by :func:`operonx.retry_call`, then again on a later loop, backing
off). A question that is answered, superseded or refused has its message
edited to say so. Reminders reply to the original; a safe default taken at
expiry, or an expired question flagged, is said too.

In (:meth:`TelegramBridge.handle`, one update): an update is handled once
(its ``update_id`` is kept); one from a user or chat not allowed is dropped
without a reply (only the user id is logged). An answer comes as a button
tap, a reply to the question's message, or plain words when exactly one
question is open; all go through :meth:`Interactions.submit_answer`, the
same path as the CLI, so a second answer or a stale one changes nothing.
Commands: ``/status``, ``/pending``, ``/milestone <id>``, ``/pause``,
``/resume``, ``/cancel <id>`` (asks for a confirming tap), ``/budget <id> +N``.

The engine never asks Telegram anything it needs to go on: when Telegram is
down the questions wait in the store and ``operonx-project answer`` works.
"""

from __future__ import annotations

import asyncio
import secrets
import time
from typing import Any, Dict, Iterable, List, Optional

from operonx import Retry, retry_call
from operonx.core.loggings import LOGGER

from operonx_project.interactions import DELIVERED, OPEN, REFUSED, SUPERSEDED, Interactions
from operonx_project.lifecycle import ACTIVE
from operonx_project.messages import LIMIT, answered_text, parse_callback, redact, render
from operonx_project.model import CardState
from operonx_project.store import Store
from operonx_project.telegram.messenger import Messenger, TelegramError

__all__ = ["TelegramBridge"]

CONFIRM_TTL = 600.0
HELP = ("/status — the milestones\n/pending — what waits on you\n/milestone <id> — one milestone's cards\n"
        "/pause [id] · /resume [id]\n/cancel <id> — asks you to confirm\n/budget <id> +N — N more dollars\n\n"
        "To answer a question: tap a button, or reply to its message in your own words.")  # fmt: skip


def _transient(error: BaseException) -> bool:
    return isinstance(error, TelegramError) and error.transient


class TelegramBridge:
    def __init__(
        self,
        store: Store,
        interactions: Interactions,
        messenger: Messenger,
        chat_id: Any,
        allowed_users: Iterable[Any],
        allowed_chats: Optional[Iterable[Any]] = None,
        project_name: str = "",
        retry: Optional[Retry] = None,
        resend_after_s: float = 30.0,
    ):
        self.store = store
        self.inter = interactions
        self.tg = messenger
        self.chat = chat_id
        self.users = {str(u) for u in allowed_users}
        self.chats = {str(c) for c in (allowed_chats if allowed_chats is not None else [chat_id])}
        self.name = project_name
        self.retry = retry or Retry(max_attempts=3, initial=0.5, max_interval=10.0, on=_transient)
        self.resend_after_s = resend_after_s
        if not self.users:
            raise ValueError("the bridge needs at least one allowed Telegram user id")

    # ── out ────────────────────────────────────────────────────────────────
    async def _send(self, chat: Any, text: str, buttons=(), reply_to: Optional[int] = None) -> int:
        return await retry_call(lambda: self.tg.send(chat, redact(text)[:LIMIT], buttons, reply_to), self.retry,
                                label="telegram send")  # fmt: skip

    async def _edit(self, chat: Any, message_id: int, text: str) -> bool:
        try:
            await retry_call(lambda: self.tg.edit(chat, message_id, redact(text)[:LIMIT]), self.retry,
                             label="telegram edit")  # fmt: skip
            return True
        except TelegramError as exc:
            LOGGER.warning(f"telegram: could not edit message {message_id}: {exc}")
            return exc.code == 400  # the message is gone or unchanged: nothing more to do

    def _around(self, inter: Dict[str, Any]):
        """Which cards go on meanwhile, which wait for this one."""
        if inter["kind"] != "card":
            return [], []
        cards = self.store.cards(inter["milestone"])
        going = [c.id for c in cards if c.state in (CardState.RUNNING, CardState.READY) and c.id != inter["card"]]
        waiting = [c.id for c in cards if inter["card"] in c.deps and c.state == CardState.PENDING]
        return going, waiting

    async def _deliver(self, inter: Dict[str, Any]) -> None:
        shown = dict(inter.get("shown") or {})
        if inter["delivery"] == "failed" and time.time() < shown.get("retry_at", 0):
            return
        text, buttons = render(inter, self.name, *self._around(inter))
        try:
            mid = await self._send(self.chat, text, buttons)
        except TelegramError as exc:
            fails = int(shown.get("fails", 0)) + 1
            shown.update(fails=fails, retry_at=time.time() + min(600.0, self.resend_after_s * 2 ** (fails - 1)),
                         error=str(exc)[:200])  # fmt: skip
            self.store.update_interaction(inter["id"], delivery="failed", shown=shown)
            self.store.note(inter["milestone"], "telegram.send_failed", {"id": inter["id"], "error": str(exc)[:200],
                            "fails": fails}, card=inter.get("card"))  # fmt: skip
            LOGGER.warning(f"telegram: {inter['qid']} not sent ({exc}); it waits in the store, retried later")
            return
        shown.update(text=text, fails=0)
        shown.pop("retry_at", None)
        msgs = [*(inter.get("messages") or []), {"chat": self.chat, "id": mid, "at": time.time()}]
        self.store.update_interaction(inter["id"], delivery="sent", messages=msgs, shown=shown)

    async def _close(self, inter: Dict[str, Any]) -> None:
        shown = dict(inter.get("shown") or {})
        original = shown.get("text", "")
        if inter["state"] == DELIVERED:
            text = answered_text(inter, original)
        elif inter["state"] == SUPERSEDED:
            text = f"⏹ No longer current (the plan or the card moved on); /pending lists what is.\n\n{original}"
        else:
            text = f"⚠ Your answer was not applied: {shown.get('refused') or 'refused'}\n\n{original}"
        done = all([await self._edit(m["chat"], m["id"], text) for m in inter["messages"]])
        if done:
            self.store.update_interaction(inter["id"], shown={**shown, "final": inter["state"]})

    async def _tick(self, iid: str, what: str) -> None:
        inter = self.store.get_interaction(iid)
        if inter is None or not inter.get("messages"):
            return
        first = inter["messages"][0]
        if what == "remind":
            text = (
                f"⏰ Still waiting on you: {inter['qid']} ({inter.get('card') or inter['kind']}). Reply to it, above."
            )
        elif what == "defaulted":
            rec = (inter.get("payload") or {}).get("recommended")
            text = f"⌛ No answer came for {inter['qid']}: I took the safe default ({rec}). Reply to it to correct me."
        else:
            text = (f"⌛ {inter['qid']} expired with no safe default: it stays open, flagged in "
                    "operonx-project status. Reply to it whenever you can.")  # fmt: skip
        try:
            await self._send(first["chat"], text, reply_to=first["id"])
        except TelegramError as exc:
            LOGGER.warning(f"telegram: the {what} for {inter['qid']} was not sent ({exc})")

    async def sync(self) -> None:
        """Open, supersede and close interactions, then bring Telegram up to date."""
        self.inter.sync()
        events = self.inter.tick()
        for inter in self.store.interactions(states=[OPEN]):
            if inter["delivery"] != "sent" and self.inter.current(inter):
                await self._deliver(inter)
        for inter in self.store.interactions(states=[DELIVERED, SUPERSEDED, REFUSED]):
            if inter.get("messages") and (inter.get("shown") or {}).get("final") != inter["state"]:
                await self._close(inter)
        for iid, what in events:
            await self._tick(iid, what)

    # ── in ─────────────────────────────────────────────────────────────────
    def _allowed(self, user: Any, chat: Any) -> bool:
        return str(user) in self.users and str(chat) in self.chats

    async def _reply(self, chat: Any, text: str, reply_to: Optional[int] = None, buttons=()) -> None:
        try:
            await self._send(chat, text, buttons, reply_to)
        except TelegramError as exc:
            LOGGER.warning(f"telegram: a reply was not sent ({exc})")

    async def handle(self, update: Dict[str, Any]) -> str:
        """One update; returns what became of it (for logs and tests)."""
        uid = update.get("update_id")
        if uid is None or not self.store.first_update(int(uid)):
            return "duplicate"
        if "callback_query" in update:
            cq = update["callback_query"]
            user, chat = (cq.get("from") or {}).get("id"), ((cq.get("message") or {}).get("chat") or {}).get("id")
        elif "message" in update:
            msg = update["message"]
            user, chat = (msg.get("from") or {}).get("id"), (msg.get("chat") or {}).get("id")
        else:
            return "ignored"
        if not self._allowed(user, chat):
            LOGGER.info(f"telegram: dropped an update from user {user}")  # the user id only, never the text
            return "dropped"
        actor = f"telegram:{user}"
        if "callback_query" in update:
            return await self._tap(update["callback_query"], chat, actor)
        msg = update["message"]
        text = str(msg.get("text") or "").strip()
        if not text:
            return "ignored"
        if text.startswith("/"):
            return await self._command(text, chat, msg["message_id"], actor)
        reply = msg.get("reply_to_message") or {}
        if reply.get("message_id"):
            inter = self.store.find_interaction_by_message(chat, reply["message_id"])
            if inter is None:
                await self._reply(chat, "That message is not one of my questions; /pending lists the open ones.",
                                  msg["message_id"])  # fmt: skip
                return "unknown"
            got = self.inter.submit_answer(inter["id"], text, actor=actor, via="telegram")
            await self._reply(chat, self._said(got), msg["message_id"])
            return got.status
        open_ = self.inter.open()
        if len(open_) == 1:
            got = self.inter.submit_answer(open_[0]["id"], text, actor=actor, via="telegram")
            if got.ok:
                which = f"{open_[0]['qid']} ({open_[0].get('card') or open_[0]['kind']})"
                await self._reply(chat, f"Taken as your answer to {which}.", msg["message_id"])
            else:
                await self._reply(chat, self._said(got), msg["message_id"])
            return got.status
        if not open_:
            await self._reply(chat, "Nothing waits on you. /status shows the milestones.", msg["message_id"])
            return "none"
        await self._reply(chat, f"{len(open_)} questions are open; reply to the one you mean (swipe its message):\n"
                          + self._list(open_), msg["message_id"])  # fmt: skip
        return "ambiguous"

    def _said(self, got) -> str:
        if got.ok:
            return f"✅ {got.message}"
        if got.status == "stale" and got.current:
            return f"⏹ {got.message}. Reply to that one's message."
        return f"⚠ {got.message}"

    def _list(self, inters: List[Dict[str, Any]]) -> str:
        out = []
        for i in inters:
            p = i.get("payload") or {}
            out.append(f"• {i['qid']} · {i['milestone']}{'/' + i['card'] if i.get('card') else ''} — "
                       f"{str(p.get('problem') or p.get('reason') or i['kind'])[:120]}")  # fmt: skip
        return "\n".join(out)

    async def _tap(self, cq: Dict[str, Any], chat: Any, actor: str) -> str:
        data = str(cq.get("data") or "")
        message_id = (cq.get("message") or {}).get("message_id")
        if data.startswith(("c:", "k:")):
            return await self._confirm(cq, data, chat, actor)
        parsed = parse_callback(data)
        inter = self.store.get_interaction(parsed[0]) if parsed else None
        if inter is None or not any(str(m["chat"]) == str(chat) and m["id"] == message_id for m in inter["messages"]):
            await self.tg.answer_callback(cq["id"], "I do not know that button.")
            return "unknown"
        _, option, version = parsed
        if version != int(inter.get("version") or 1):
            got_status, text = "stale", "This button belongs to an older version of the question."
        else:
            got = self.inter.submit_answer(inter["id"], {"choice": option, "text": ""}, actor=actor, via="telegram")
            got_status, text = got.status, self._said(got)
        try:
            await self.tg.answer_callback(cq["id"], text)
        except TelegramError as exc:
            LOGGER.warning(f"telegram: a button answer was not acknowledged ({exc})")
        return got_status

    # ── commands ───────────────────────────────────────────────────────────
    def _one_active(self, arg: str) -> Optional[str]:
        if arg:
            return arg
        active = [m.id for m in self.store.milestones() if m.state in ACTIVE]
        return active[0] if len(active) == 1 else None

    async def _command(self, text: str, chat: Any, message_id: int, actor: str) -> str:
        word, _, rest = text.partition(" ")
        word = word.split("@", 1)[0].lower()
        rest = rest.strip()
        if word == "/status":
            lines = []
            for ms in self.store.milestones():
                cards = self.store.cards(ms.id)
                done = sum(c.state == CardState.DONE for c in cards)
                lines.append(f"{ms.id} · {ms.state.value} · {ms.title} — {done}/{len(cards)} cards, "
                             f"${ms.cost_usd:.2f} of ${ms.policy.budget.max_usd:.0f}")  # fmt: skip
            await self._reply(chat, "\n".join(lines) or "No milestone yet.", message_id)
        elif word == "/pending":
            open_ = self.inter.open()
            await self._reply(chat, self._list(open_) if open_ else "Nothing waits on you.", message_id)
        elif word == "/milestone":
            try:
                ms = self.store.get_milestone(rest)
            except KeyError:
                await self._reply(chat, f"No milestone {rest!r}.", message_id)
                return "unknown"
            lines = [f"{ms.id} · {ms.state.value} · {ms.title}" + (f"\n{ms.reason[:300]}" if ms.reason else "")]
            lines += [f"• {c.id} {c.state.value} — {c.title[:60]}" for c in self.store.cards(ms.id)]
            await self._reply(chat, "\n".join(lines), message_id)
        elif word in ("/pause", "/resume"):
            mid = self._one_active(rest)
            if mid is None:
                await self._reply(chat, f"Which milestone? {word} <id>", message_id)
                return "ambiguous"
            kind = "pause" if word == "/pause" else "unpause"
            seq = self.store.enqueue(mid, kind, actor=actor)
            await self._reply(chat, f"Queued {kind} for {mid} (#{seq}).", message_id)
        elif word == "/cancel":
            if not rest:
                await self._reply(chat, "Which milestone? /cancel <id>", message_id)
                return "ambiguous"
            try:
                ms = self.store.get_milestone(rest)
            except KeyError:
                await self._reply(chat, f"No milestone {rest!r}.", message_id)
                return "unknown"
            nonce = secrets.token_hex(6)
            self.store.save_checkpoint(
                f"telegram:confirm:{nonce}", {"mid": ms.id, "chat": str(chat), "at": time.time()}
            )
            await self._reply(
                chat,
                f"Cancel {ms.id} ({ms.title})? Its cards stop; merged work stays.",
                message_id,
                buttons=[[(f"Yes, cancel {ms.id}", f"c:{nonce}"), ("No, keep it", f"k:{nonce}")]],
            )
            return "confirm"
        elif word == "/budget":
            mid, _, amount = rest.partition(" ")
            try:
                usd = float(amount.strip().lstrip("+").lstrip("$"))
                self.store.get_milestone(mid)
            except (ValueError, KeyError):
                await self._reply(chat, "Usage: /budget <milestone> +N (dollars)", message_id)
                return "invalid"
            budget = [i for i in self.inter.open(mid) if i["kind"] == "budget"]
            if budget:
                got = self.inter.submit_answer(budget[0]["id"], f"+{usd:g}", actor=actor, via="telegram")
                await self._reply(chat, self._said(got), message_id)
                return got.status
            seq = self.store.enqueue(mid, "retry", {"usd": usd}, actor=actor)
            await self._reply(chat, f"Queued +${usd:g} for {mid} (#{seq}).", message_id)
        else:
            await self._reply(chat, HELP, message_id)
            return "help"
        return "ok"

    async def _confirm(self, cq: Dict[str, Any], data: str, chat: Any, actor: str) -> str:
        nonce = data[2:]
        rec = self.store.checkpoint(f"telegram:confirm:{nonce}")
        if not rec or rec.get("used") or rec.get("chat") != str(chat) or time.time() - rec["at"] > CONFIRM_TTL:
            await self.tg.answer_callback(cq["id"], "That confirmation has expired; send /cancel again.")
            return "stale"
        self.store.save_checkpoint(f"telegram:confirm:{nonce}", {**rec, "used": True})
        message_id = (cq.get("message") or {}).get("message_id")
        if data.startswith("k:"):
            await self.tg.answer_callback(cq["id"], "Kept.")
            if message_id:
                await self._edit(chat, message_id, f"Kept {rec['mid']}.")
            return "kept"
        seq = self.store.enqueue(rec["mid"], "cancel", {"why": "the owner cancelled it from Telegram"}, actor=actor)
        await self.tg.answer_callback(cq["id"], f"Cancelling {rec['mid']}.")
        if message_id:
            await self._edit(chat, message_id, f"Cancelling {rec['mid']} (#{seq}).")
        return "cancelled"

    # ── the long poll ──────────────────────────────────────────────────────
    async def poll(self, timeout: float = 25.0) -> int:
        """One ``getUpdates``; each update handled and the offset kept, so a
        restart neither loses nor repeats one."""
        offset = int((self.store.checkpoint("telegram:offset") or {}).get("offset", 0))
        try:
            got = await self.tg.updates(offset, timeout)
        except TelegramError as exc:
            LOGGER.warning(f"telegram: getUpdates failed ({exc})")
            await asyncio.sleep(min(5.0, timeout) if exc.transient else 30.0)
            return 0
        for u in got:
            try:
                what = await self.handle(u)
                LOGGER.debug(f"telegram: update {u.get('update_id')}: {what}")
            except Exception as exc:  # one bad update must not stop the poll
                LOGGER.error(f"telegram: update {u.get('update_id')} raised {type(exc).__name__}: {exc}")
            self.store.save_checkpoint("telegram:offset", {"offset": int(u["update_id"]) + 1})
        return len(got)

    async def run(self, stop: Optional[asyncio.Event] = None, timeout: float = 25.0) -> None:
        while not (stop and stop.is_set()):
            await self.poll(timeout)
