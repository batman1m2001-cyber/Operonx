"""Failure policies: what an op does when it fails, and what the run does.

Declared, never coded into an op body::

    from operonx import Operon, Retry, Timeout, op

    @op(retry=Retry(max_attempts=4), timeout=Timeout(run=30))
    async def call_crm(order_id: str) -> dict: ...

    Operon(g, errors="raise", max_concurrency=32)

* :class:`Retry` — attempts, backoff and which errors are worth another try.
* :class:`Timeout` — a deadline per attempt (``run``) and, for a generator,
  between yields (``idle``).
* :data:`TRANSIENT` — the default ``Retry(on=...)``: timeouts, connection
  errors, HTTP 429 and 5xx.
* :class:`OpFailed` — what ``Operon(g, errors="raise")`` raises.

Failures the op does not retry are recorded as they always were (``"$errors"``,
the op's ``error`` cell, a failed trace node) unless the run asked to fail fast.
See ``docs/RUNTIME_R1_PLAN.md`` for the design.
"""

from __future__ import annotations

import asyncio
import random
import sys
from dataclasses import dataclass
from typing import Any, Callable, NamedTuple, Optional, Tuple, Type, Union

__all__ = [
    "TRANSIENT",
    "OpFailed",
    "Retry",
    "Timeout",
    "mark_retried",
]

#: Set on an error an inner layer has already retried to exhaustion.
_RETRIED_ATTR = "__operonx_retried__"


def mark_retried(error: BaseException) -> BaseException:
    """Note that *error* already went through a retry loop, and return it.

    A layer that retries a call itself — ``LLMOp``'s transport retry, driven
    by the resource's ``max_retries`` — marks the error it finally gives up
    on, so an op's ``retry=Retry(...)`` around it does not retry it again.
    Retrying at both layers multiplies the calls (3 transport retries under
    4 op attempts is 16 requests) for an error that was already given every
    chance::

        except openai.APIConnectionError as e:
            if attempt == max_retries:
                raise mark_retried(e)
    """
    try:
        setattr(error, _RETRIED_ATTR, True)
    except AttributeError:
        # An exception type with __slots__ cannot carry the mark; it stays
        # retriable rather than becoming unraisable here.
        pass
    return error


def _http_status(error: BaseException) -> Optional[int]:
    """The HTTP status an error carries, by the attribute names clients use."""
    for attr in ("status_code", "status"):  # openai/httpx-style, aiohttp
        value = getattr(error, attr, None)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    value = getattr(getattr(error, "response", None), "status_code", None)  # httpx.HTTPStatusError
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return None


class _Transient:
    """Is this error worth another attempt? The default of ``Retry(on=...)``.

    Yes for a timeout, a connection failure, and an HTTP 429 or 5xx — the
    errors a second attempt can get past. No for everything else: a
    ``ValueError`` or a 400 fails the same way every time, and retrying it
    only spends the quota.

    ``httpx`` and ``openai`` transport errors count as connection failures
    when those packages are already imported; this module never imports
    them.
    """

    __slots__ = ()

    def __call__(self, error: BaseException) -> bool:
        if isinstance(error, (TimeoutError, asyncio.TimeoutError, ConnectionError)):
            return True
        status = _http_status(error)
        if status is not None:
            return status == 429 or 500 <= status <= 599
        httpx = sys.modules.get("httpx")
        if httpx is not None and isinstance(error, httpx.TransportError):
            return True
        openai = sys.modules.get("openai")
        if openai is not None and isinstance(error, openai.APIConnectionError):
            return True
        return False

    def __repr__(self) -> str:
        return "TRANSIENT"


#: The default ``Retry(on=...)``: timeouts, connection errors, HTTP 429 and 5xx.
TRANSIENT: Callable[[BaseException], bool] = _Transient()

RetryOn = Union[
    Type[BaseException], Tuple[Type[BaseException], ...], Callable[[BaseException], bool]
]


def _is_exception_type(value: Any) -> bool:
    return isinstance(value, type) and issubclass(value, BaseException)


@dataclass(frozen=True)
class Retry:
    """Run a failed op again, after a growing pause.

    ``@op(retry=Retry(...))`` or, for one use of the op, ``my_op(x=..., retry=Retry(...))``.

    Args:
        max_attempts: attempts in all, the first included (``1`` = no retry).
        initial: seconds to wait after the first failed attempt.
        backoff: each later wait is this many times the one before.
        max_interval: no wait is longer than this.
        jitter: wait a random time between half the computed wait and all
            of it, so many runs failing together do not retry together.
        on: which errors to retry: an exception class, a tuple of them, or a
            predicate ``error -> bool``. Defaults to :data:`TRANSIENT`.

    Never retried, whatever ``on`` says:

    * a generator that has already yielded — its items have been handed on,
      and a second attempt would hand them on again; the error is recorded;
    * an error an inner layer already retried (see :func:`mark_retried`);
    * cancellation, and other ``BaseException``\\ s.
    """

    max_attempts: int = 3
    initial: float = 0.5
    backoff: float = 2.0
    max_interval: float = 30.0
    jitter: bool = True
    on: RetryOn = TRANSIENT

    def __post_init__(self) -> None:
        if isinstance(self.max_attempts, bool) or not isinstance(self.max_attempts, int):
            raise TypeError(f"Retry(max_attempts=...) takes an int, got {self.max_attempts!r}")
        if self.max_attempts < 1:
            raise ValueError(
                f"Retry(max_attempts={self.max_attempts}): it counts the first attempt too, "
                f"so it is at least 1"
            )
        for name in ("initial", "backoff", "max_interval"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
                raise ValueError(f"Retry({name}=...) takes a number >= 0, got {value!r}")
        if self.backoff < 1:
            raise ValueError(
                f"Retry(backoff={self.backoff}): waits must not shrink; use 1 for a fixed wait"
            )
        on = self.on
        valid = (
            _is_exception_type(on)
            or (isinstance(on, tuple) and on and all(_is_exception_type(t) for t in on))
            or (callable(on) and not isinstance(on, type))
        )
        if not valid:
            raise TypeError(
                f"Retry(on=...) takes an exception class, a tuple of them, or a function "
                f"error -> bool; got {on!r}"
            )

    def retries(self, error: BaseException) -> bool:
        """Is *error* one this policy retries (attempts left aside)?"""
        if getattr(error, _RETRIED_ATTR, False):
            return False
        on = self.on
        if isinstance(on, (type, tuple)):
            return isinstance(error, on)
        return bool(on(error))

    def delay(self, attempt: int) -> float:
        """Seconds to wait after failed attempt number *attempt* (1-based)."""
        wait = min(self.max_interval, self.initial * self.backoff ** (attempt - 1))
        if self.jitter:
            wait = random.uniform(wait / 2, wait)
        return wait


@dataclass(frozen=True)
class Timeout:
    """A deadline for one attempt of an op.

    Args:
        run: seconds an attempt may take, start to end. For a generator the
            clock keeps running while the consumer holds an item, but it can
            only stop the generator while the generator is working.
        idle: generators only: seconds the generator may take to produce
            its next item.

    An attempt past its deadline is cancelled and fails with
    ``TimeoutError``, recorded like any failure: the op's outputs are
    missing and the ops after it do not run. ``TimeoutError`` is
    :data:`TRANSIENT`, so a ``retry=`` tries again.

    An op that runs on a worker thread (``bound="cpu"``) is abandoned at the
    deadline: the run moves on, the thread finishes in the background.
    A plain ``def`` op runs on the event loop itself, where nothing can stop
    it, so it takes a timeout only with ``bound="cpu"``.
    """

    run: Optional[float] = None
    idle: Optional[float] = None

    def __post_init__(self) -> None:
        if self.run is None and self.idle is None:
            raise ValueError("Timeout() needs run=, idle=, or both")
        for name in ("run", "idle"):
            value = getattr(self, name)
            if value is None:
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
                raise ValueError(f"Timeout({name}=...) takes seconds > 0, got {value!r}")


class OpPolicy(NamedTuple):
    """An op's ``retry=`` and ``timeout=``, as one value (``None`` when neither is set)."""

    retry: Optional[Retry]
    timeout: Optional[Timeout]


def op_policy(retry: Any, timeout: Any) -> Optional[OpPolicy]:
    """Check ``retry=`` / ``timeout=`` as given to an op, and pair them."""
    if retry is not None and not isinstance(retry, Retry):
        hint = f"Retry(max_attempts={retry})" if isinstance(retry, int) else "Retry(...)"
        raise TypeError(f"retry= takes a Retry, as in retry={hint}; got {retry!r}")
    if timeout is not None and not isinstance(timeout, Timeout):
        hint = (
            f"Timeout(run={timeout})"
            if isinstance(timeout, (int, float)) and not isinstance(timeout, bool)
            else "Timeout(run=...)"
        )
        raise TypeError(f"timeout= takes a Timeout, as in timeout={hint}; got {timeout!r}")
    if retry is None and timeout is None:
        return None
    return OpPolicy(retry, timeout)


class OpFailed(Exception):
    """An op failed in a run started with ``Operon(g, errors="raise")``.

    ``op`` is the op's full name (its key in ``"$errors"``) and ``error``
    the message recorded for it there (``"$errors"[op]["message"]``); the
    original exception is ``__cause__``.
    The run was stopped: the ops still running were cancelled.
    """

    def __init__(self, op: str, error: str) -> None:
        self.op = op
        self.error = error
        last = error.strip().splitlines()[-1] if error and error.strip() else error
        super().__init__(f"{op} failed: {last}")


class _FailFast(BaseException):
    """Carries an op failure out of a ``errors="raise"`` run.

    A ``BaseException`` so the ``except Exception`` of every enclosing op —
    a subgraph records its own failures there — lets it through to the
    scheduler, whose ``fatal`` path cancels the run. The engine turns it
    into :class:`OpFailed` for the caller.
    """

    def __init__(self, failure: OpFailed) -> None:
        super().__init__(str(failure))
        self.failure = failure


def fail_fast(state: Any, op: str, cause: Optional[BaseException]) -> _FailFast:
    """The carrier to raise for *op*'s failure in a ``errors="raise"`` run.

    Called right after ``state.record_op_error``: ``OpFailed.error`` is the
    message that record holds, the same text as ``"$errors"[op]["message"]``.
    """
    failure = OpFailed(op, state._op_errors[op]["message"])
    failure.__cause__ = cause
    return _FailFast(failure)


@dataclass
class RunPolicy:
    """What a run does on failure and how many ops it runs at once.

    Created by ``Operon.start`` for each run and kept on its
    ``MemoryState``; ``None`` there (an op called directly) means the
    defaults: record and carry on, no shared limit.
    """

    errors: str = "record"
    limiter: Optional[asyncio.Semaphore] = None

    @property
    def fail_fast(self) -> bool:
        return self.errors == "raise"


ERRORS_MODES = ("record", "raise")


class _Deadline:
    """``async with _Deadline(at, message)`` — ``asyncio.timeout`` for 3.10 too.

    Cancels the current task at loop time *at* and turns that cancellation
    into ``TimeoutError(message)``. On Python 3.11+ ``Task.uncancel`` tells
    the deadline's own cancellation from one requested by someone else,
    which then propagates as it should.
    """

    __slots__ = ("_at", "_message", "_task", "_handle", "_expired")

    def __init__(self, at: float, message: str) -> None:
        self._at = at
        self._message = message
        self._expired = False
        self._task = None
        self._handle = None

    async def __aenter__(self) -> "_Deadline":
        loop = asyncio.get_running_loop()
        if self._at <= loop.time():
            raise TimeoutError(self._message)
        self._task = asyncio.current_task()
        self._handle = loop.call_at(self._at, self._expire)
        return self

    def _expire(self) -> None:
        self._expired = True
        self._task.cancel()

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        self._handle.cancel()
        if not self._expired:
            return False
        uncancel = getattr(self._task, "uncancel", None)
        if uncancel is not None and uncancel() > 0:
            # Cancelled from outside as well: that one wins.
            return False
        if exc_type is None or issubclass(exc_type, asyncio.CancelledError):
            raise TimeoutError(self._message) from None
        return False
