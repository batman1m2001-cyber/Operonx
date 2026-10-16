"""Guards on resources: a check that runs before every call of a resource, whichever op makes it.

    from operonx.core.registry import ResourceHub

    def no_secrets(key, method, args, kwargs):
        if "password" in str(kwargs.get("messages") or args):
            raise PermissionError(f"{key}: a password does not leave the building")

    ResourceHub.guard("llm:", no_secrets)

A project's rule about what may be sent to a resource — client data never to an outside model, a cap on what one
call may carry — has to hold for every call, and the calls come from many places: ``LLMOp``, an agent's model,
a tool, a hand-written op. So the rule sits on the resource, like ``rate_limit:``: :class:`ResourceHub` wraps the
instance it hands out, and each public async method (coroutines, and async generators, checked before their
first item) runs the guards whose prefix the key starts with, in the order they were added, before the call
starts — and before it waits for a rate-limit slot. A guard is ``fn(key, method, args, kwargs)``, plain or async;
what it raises is what the caller gets, and the call is not made. Guards are for the whole process; one added
after a resource was handed out applies to that instance too.
"""

from __future__ import annotations

import functools
import inspect
import threading
from typing import Any, Callable, List, Tuple

from operonx.core.loggings import LOGGER

__all__ = ["Guard", "add_guard", "guards_for", "guard_instance"]

#: ``fn(key, method, args, kwargs)``; raise to refuse the call.
Guard = Callable[[str, str, tuple, dict], Any]

_GUARDS: List[Tuple[str, Guard]] = []
_LOCK = threading.Lock()
_MARK = "_operonx_guarded"


def add_guard(prefix: str, fn: Guard) -> Callable[[], None]:
    """Run *fn* before every call of a resource whose key starts with *prefix*; returns a function that
    removes it again."""
    if not callable(fn):
        raise TypeError(f"a guard is a callable, got {fn!r}")
    entry = (str(prefix), fn)
    with _LOCK:
        _GUARDS.append(entry)

    def remove() -> None:
        with _LOCK:
            if entry in _GUARDS:
                _GUARDS.remove(entry)

    return remove


def guards_for(key: str) -> List[Guard]:
    """The guards on *key*, in the order they were added."""
    with _LOCK:
        return [fn for prefix, fn in _GUARDS if key.startswith(prefix)]


async def _check(key: str, method: str, args: tuple, kwargs: dict) -> None:
    for fn in guards_for(key):
        out = fn(key, method, args, kwargs)
        if inspect.isawaitable(out):
            await out


def guard_instance(instance: Any, *, key: str) -> Any:
    """Make each public async method of *instance* run *key*'s guards first (looked up at each call, so a guard
    added later counts). Set on the instance itself, once; its class and ``isinstance`` are as they were."""
    if getattr(instance, _MARK, None) == key:
        return instance
    for name in dir(type(instance)):
        if name.startswith("_"):
            continue
        method = getattr(type(instance), name, None)
        if inspect.isasyncgenfunction(method):
            wrapped = _guarded_gen(getattr(instance, name), key, name)
        elif inspect.iscoroutinefunction(method):
            wrapped = _guarded(getattr(instance, name), key, name)
        else:
            continue
        try:
            setattr(instance, name, wrapped)
        except (AttributeError, TypeError):
            LOGGER.warning(
                "guard %s: %s.%s cannot be wrapped (slots?); its calls are not checked",
                key,
                type(instance).__name__,
                name,
            )
    try:
        setattr(instance, _MARK, key)
    except (AttributeError, TypeError):
        pass
    return instance


def _guarded(bound: Any, key: str, name: str) -> Any:
    @functools.wraps(bound)
    async def call(*args: Any, **kwargs: Any) -> Any:
        await _check(key, name, args, kwargs)
        return await bound(*args, **kwargs)

    return call


def _guarded_gen(bound: Any, key: str, name: str) -> Any:
    @functools.wraps(bound)
    async def call(*args: Any, **kwargs: Any) -> Any:
        await _check(key, name, args, kwargs)
        async for item in bound(*args, **kwargs):
            yield item

    return call
