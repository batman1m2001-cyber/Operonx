"""The daemon: one per project, by lease; it runs the owner's commands and drives milestones.

The CLI, the studio and (later) Telegram never drive a milestone: they
queue a command in the store (:meth:`~operonx_project.store.Store.enqueue`).
The daemon takes the lease (a stale one expires after its TTL, so a killed
daemon is replaced within 30 s), runs the recovery pass, and then loops:
commands first, then a drive task for every milestone in an active state.
A command leaves the queue only once it was applied, so a daemon killed
between an answer and the resume finds the answer still queued, or on the
card (crash matrix, plan §10).

The queue is a SQLite table written by other processes, so the loop polls
it (``poll_s``, default 0.5 s); everything inside a drive is event-driven.
Each loop also brings the owner's questions up to date
(:class:`~operonx_project.interactions.Interactions`) and, with a
:class:`~operonx_project.telegram.TelegramBridge`, sends them; the bridge's
long poll runs beside the loop.
"""

from __future__ import annotations

import asyncio
import os
import socket
import time
import uuid
from typing import Any, Dict, Optional

from operonx.core.loggings import LOGGER

from operonx_project.interactions import Interactions
from operonx_project.lifecycle import ACTIVE, Lifecycle, Refused
from operonx_project.model import MilestoneState as M
from operonx_project.scheduler import _kill_group

__all__ = ["Daemon", "DaemonBusy"]


class DaemonBusy(RuntimeError):
    """Another daemon holds the project's lease."""


class Daemon:
    def __init__(self, life: Lifecycle, owner: Optional[str] = None, poll_s: float = 0.5, lease_ttl: float = 30.0,
                 interactions: Optional[Interactions] = None, bridge: Any = None):  # fmt: skip
        self.life = life
        self.store = life.store
        self.interactions = interactions or Interactions(life.store, project=life.p)
        self.bridge = bridge
        self.owner = owner or f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:6]}"
        self.poll_s = poll_s
        self.ttl = lease_ttl
        self.drives: Dict[str, asyncio.Task] = {}
        self._wakes: Dict[str, tuple] = {}  # mid → (timer, the resume_at it was set for)

    def recover(self) -> None:
        """The recovery pass: kill the coder sessions a dead daemon left in a
        stage (the cards' are the scheduler's); the journals do the rest when
        each active milestone is driven again."""
        for ms in self.store.milestones():
            if ms.state not in ACTIVE:
                continue
            for name in ("survey", "plan-1", "plan-2", "plan-3", "milestone-review"):
                s = self.store.ms_get(ms.id, f"session:{name}") or {}
                if s.get("pgid"):
                    _kill_group(int(s["pgid"]))
            self.store.note(ms.id, "daemon.recover", {"state": ms.state.value, "owner": self.owner})

    async def _lease(self) -> None:
        while True:
            await asyncio.sleep(self.ttl / 3)
            if not self.store.acquire_lease(self.owner, self.ttl):
                LOGGER.error("the daemon lost its lease; stopping")
                for t in self.drives.values():
                    t.cancel()
                return

    async def _commands(self) -> int:
        n = 0
        for cmd in self.store.pending_commands():
            n += 1
            try:
                result = await self.life.apply(cmd)
                self.store.finish_command(cmd["seq"], "done", result)
            except (Refused, KeyError, ValueError) as exc:
                self.store.finish_command(cmd["seq"], "refused", str(exc))
                self.store.note(cmd["milestone"], "command.refused", {"kind": cmd["kind"], "why": str(exc)[:500]})
        return n

    async def _questions(self) -> None:
        """The owner's questions: opened, closed, reminded; sent when a bridge is on."""
        try:
            if self.bridge is not None:
                await self.bridge.sync()
            else:
                self.interactions.sync()
                self.interactions.tick()
        except Exception as exc:  # a question that cannot be sent must not stop the milestones
            LOGGER.error(f"the questions' sync raised {type(exc).__name__}: {exc}")

    def _wake_timers(self) -> None:
        """A milestone paused by a rate limit goes on at its ``resume_at``: a timer (made
        again by a restarted daemon from the store), which queues the unpause."""
        loop = asyncio.get_running_loop()
        for ms in self.store.milestones():
            due = ms.resume_at if ms.state == M.PAUSED else None
            have = self._wakes.get(ms.id)
            if have is not None and have[1] != due:
                have[0].cancel()
                self._wakes.pop(ms.id)
                have = None
            if due is not None and have is None:
                self._wakes[ms.id] = (loop.call_later(max(0.0, due - time.time()), self._wake, ms.id, due), due)

    def _wake(self, mid: str, due: float) -> None:
        self._wakes.pop(mid, None)
        ms = self.store.get_milestone(mid)
        if ms.state == M.PAUSED and ms.resume_at == due:
            self.store.enqueue(mid, "unpause", {"why": "the rate limit reset"}, actor="engine")

    def _drive_active(self) -> None:
        for ms in self.store.milestones():
            if ms.state in ACTIVE and (ms.id not in self.drives or self.drives[ms.id].done()):
                self.drives[ms.id] = asyncio.ensure_future(self.life.drive(ms.id))

    async def serve(self, once: bool = False, stop: Optional[asyncio.Event] = None) -> None:
        """Run until *stop* is set; with *once*, until nothing is active and no command is queued."""
        if not self.store.acquire_lease(self.owner, self.ttl):
            raise DaemonBusy("another daemon is driving this project (its lease has not expired)")
        lease = asyncio.ensure_future(self._lease())
        polling = asyncio.ensure_future(self.bridge.run(stop)) if self.bridge is not None else None
        try:
            self.recover()
            while not (stop and stop.is_set()):
                await self._commands()
                await self._questions()
                self._wake_timers()
                self._drive_active()
                for mid, t in list(self.drives.items()):
                    if t.done():
                        self.drives.pop(mid)
                        if t.exception() is not None:
                            LOGGER.error(f"{mid}: the drive raised {t.exception()!r}")
                if once and not self.drives and not self.store.pending_commands():
                    self._wake_timers()  # a drive that just ended in a rate-limit pause leaves a wake to wait for
                    if not self._wakes and not any(m.state in ACTIVE for m in self.store.milestones()):
                        await self._questions()  # the last closes: "answered", "no longer current"
                        return
                waiting = [*self.drives.values()]
                if waiting:
                    await asyncio.wait(waiting, timeout=self.poll_s, return_when=asyncio.FIRST_COMPLETED)
                else:
                    await asyncio.sleep(self.poll_s)
        finally:
            lease.cancel()
            for h, _ in self._wakes.values():
                h.cancel()
            self._wakes.clear()
            if polling is not None:
                polling.cancel()
            for t in self.drives.values():
                t.cancel()
            await asyncio.gather(*self.drives.values(), return_exceptions=True)
            self.store.release_lease(self.owner)
