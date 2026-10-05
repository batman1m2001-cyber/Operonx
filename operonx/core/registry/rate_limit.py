"""``rate_limit:`` on a resource: how many calls run at once, and how many
start per second or minute — for the whole process, whichever op calls.

    llm:gpt-4o:
      api_type: openai
      rate_limit: {concurrency: 8, per_minute: 500}

A ``max_concurrency`` cap is per run and counts ops; a provider's limit is
per key and counts calls, from every run, every op and every nested graph
at once. So the limit sits on the resource: :class:`ResourceHub` wraps the
instance it hands out, and each of the instance's public async methods
(coroutines, and async generators — which hold their slot until they end)
waits its turn. A call made from inside another call to the same resource
(``generate_batch`` calling ``generate``) passes straight through: it is
part of the call already counted, and waiting on itself would never end.
"""

from __future__ import annotations

import asyncio
import functools
import inspect
import threading
import time
import weakref
from collections import deque
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Dict, FrozenSet, Optional

from operonx.core.loggings import LOGGER

__all__ = ["RateLimit", "Limiter", "limit_instance"]

#: The resources whose limited call this task is inside.
_INSIDE: ContextVar[FrozenSet[str]] = ContextVar("operonx_rate_limited", default=frozenset())


@dataclass(frozen=True)
class RateLimit:
    """At most ``concurrency`` calls at once, and at most ``per_second`` (or
    ``per_minute``) started in any such window. Any may be left out."""

    concurrency: Optional[int] = None
    per_second: Optional[float] = None
    per_minute: Optional[float] = None

    @classmethod
    def parse(cls, raw: Any, *, key: str) -> "RateLimit":
        if not isinstance(raw, dict):
            raise ValueError(f"{key}: rate_limit is a mapping, got {raw!r}")
        unknown = sorted(set(raw) - {"concurrency", "per_second", "per_minute"})
        if unknown:
            raise ValueError(
                f"{key}: rate_limit has {unknown}; it takes concurrency, per_second, per_minute"
            )
        if raw.get("per_second") is not None and raw.get("per_minute") is not None:
            raise ValueError(f"{key}: rate_limit takes per_second or per_minute, not both")
        for name in ("concurrency", "per_second", "per_minute"):
            value = raw.get(name)
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0
            ):
                raise ValueError(f"{key}: rate_limit {name}={value!r}; a number above zero")
        concurrency = raw.get("concurrency")
        if concurrency is not None and int(concurrency) != concurrency:
            raise ValueError(f"{key}: rate_limit concurrency={concurrency!r}; a whole number")
        return cls(
            concurrency=int(concurrency) if concurrency is not None else None,
            per_second=raw.get("per_second"),
            per_minute=raw.get("per_minute"),
        )

    @property
    def window(self) -> Optional[tuple]:
        """``(calls, seconds)``: at most that many starts per window."""
        if self.per_second is not None:
            return self.per_second, 1.0
        if self.per_minute is not None:
            return self.per_minute, 60.0
        return None


class Limiter:
    """One resource's limit, shared by every event loop in the process."""

    def __init__(self, limit: RateLimit, key: str):
        self.limit, self.key = limit, key
        self._lock = threading.Lock()
        self._starts: deque = deque()
        self._running = 0
        # a waiter per loop: an asyncio primitive belongs to one loop
        self._freed: "weakref.WeakKeyDictionary[Any, asyncio.Event]" = weakref.WeakKeyDictionary()

    def _event(self) -> asyncio.Event:
        loop = asyncio.get_running_loop()
        event = self._freed.get(loop)
        if event is None:
            event = self._freed[loop] = asyncio.Event()
        return event

    def _try(self) -> Optional[float]:
        """Take a slot now, or say how long to wait (``inf``: until one is
        freed)."""
        now = time.monotonic()
        with self._lock:
            cap = self.limit.concurrency
            if cap is not None and self._running >= cap:
                return float("inf")
            window = self.limit.window
            if window is not None:
                calls, seconds = window
                while self._starts and now - self._starts[0] >= seconds:
                    self._starts.popleft()
                if len(self._starts) >= calls:
                    return self._starts[0] + seconds - now
                self._starts.append(now)
            self._running += 1
            return None

    async def acquire(self) -> None:
        waited = 0.0
        while True:
            wait = self._try()
            if wait is None:
                if waited > 1.0:
                    LOGGER.debug("rate_limit %s: waited %.1fs for a slot", self.key, waited)
                return
            event = self._event()
            event.clear()
            t0 = time.monotonic()
            try:
                await asyncio.wait_for(event.wait(), None if wait == float("inf") else wait)
            except asyncio.TimeoutError:
                pass
            waited += time.monotonic() - t0

    def release(self) -> None:
        with self._lock:
            self._running -= 1
        for loop, event in list(self._freed.items()):
            if loop.is_closed():
                continue
            try:
                loop.call_soon_threadsafe(event.set)
            except RuntimeError:  # closed meanwhile
                pass


_LIMITERS: Dict[str, Limiter] = {}
_LIMITERS_LOCK = threading.Lock()


def limiter_for(key: str, limit: RateLimit) -> Limiter:
    """The process's one limiter for resource *key*."""
    with _LIMITERS_LOCK:
        found = _LIMITERS.get(key)
        if found is None or found.limit != limit:
            found = _LIMITERS[key] = Limiter(limit, key)
        return found


def limit_instance(instance: Any, limit: RateLimit, *, key: str) -> Any:
    """Make each public async method of *instance* wait on *key*'s limiter.
    Set on the instance itself, so its class and ``isinstance`` are as
    they were."""
    limiter = limiter_for(key, limit)
    for name in dir(type(instance)):
        if name.startswith("_"):
            continue
        method = getattr(type(instance), name, None)
        if inspect.isasyncgenfunction(method):
            wrapped = _limited_gen(getattr(instance, name), limiter)
        elif inspect.iscoroutinefunction(method):
            wrapped = _limited(getattr(instance, name), limiter)
        else:
            continue
        try:
            setattr(instance, name, wrapped)
        except (AttributeError, TypeError):
            LOGGER.warning(
                "rate_limit %s: %s.%s cannot be wrapped (slots?); its calls are not limited",
                key,
                type(instance).__name__,
                name,
            )
    return instance


def _limited(bound: Any, limiter: Limiter) -> Any:
    @functools.wraps(bound)
    async def call(*args: Any, **kwargs: Any) -> Any:
        inside = _INSIDE.get()
        if limiter.key in inside:
            return await bound(*args, **kwargs)
        await limiter.acquire()
        token = _INSIDE.set(inside | {limiter.key})
        try:
            return await bound(*args, **kwargs)
        finally:
            _INSIDE.reset(token)
            limiter.release()

    return call


def _limited_gen(bound: Any, limiter: Limiter) -> Any:
    @functools.wraps(bound)
    async def call(*args: Any, **kwargs: Any) -> Any:
        inside = _INSIDE.get()
        if limiter.key in inside:
            async for item in bound(*args, **kwargs):
                yield item
            return
        await limiter.acquire()
        source = bound(*args, **kwargs)
        try:
            while True:
                # marked only while the source runs: between items the
                # caller's own calls to the resource count as calls
                token = _INSIDE.set(inside | {limiter.key})
                try:
                    item = await source.__anext__()
                except StopAsyncIteration:
                    return
                finally:
                    _INSIDE.reset(token)
                yield item
        finally:
            await source.aclose()
            limiter.release()

    return call
