"""``deadline(seconds)`` — ``asyncio.timeout`` with the 3.10 backport.

operonx's floor is Python 3.10, which has no ``asyncio.timeout``. The
backport does what the stdlib one does: cancel the current task when the
time passes and turn that cancellation into ``TimeoutError`` on exit, so
the work inside sees a plain cancel and cleans up.
"""

from __future__ import annotations

import asyncio
import sys
from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional

__all__ = ["deadline"]


if sys.version_info >= (3, 11):

    def deadline(seconds: Optional[float]):
        return asyncio.timeout(seconds)

else:  # pragma: no cover - exercised on 3.10 only

    @asynccontextmanager
    async def deadline(seconds: Optional[float]) -> AsyncIterator[None]:
        if seconds is None:
            yield
            return
        task = asyncio.current_task()
        fired = False

        def cut() -> None:
            nonlocal fired
            fired = True
            task.cancel()

        handle = asyncio.get_running_loop().call_later(seconds, cut)
        try:
            yield
        except asyncio.CancelledError:
            if fired:
                raise TimeoutError() from None
            raise
        finally:
            handle.cancel()
