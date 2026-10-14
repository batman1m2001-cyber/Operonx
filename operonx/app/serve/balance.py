"""Spread a pooled listener's connections evenly over its workers.

The workers of ``workers=N`` share one listening socket, and the kernel
gives a new connection to whichever worker wakes first — nothing balances
them. A callbot load test put 10 of 20 calls on one of 4 workers and none
on another; the loaded worker fell 4 s behind real time while the others
idled (docs/CCU_CAPACITY_PLAN.md, phase 4).

Each worker writes its open connections and a heartbeat into a small
shared table, and listens only while it holds no more than the least
loaded live worker: one above it stops accepting until it is not. The
least loaded worker always listens, so a connection is never refused or
left waiting; a worker whose heartbeat stops is left out of the minimum,
so a hung worker cannot hold the others back. Nothing is moved: pausing
closes this worker's copy of the listening socket (``os.dup``), resuming
listens on a new copy, and the connections it already has keep running.

``OPERONX_SERVE_BALANCE=off`` serves with plain uvicorn workers.
"""

from __future__ import annotations

import asyncio
import os
import socket
import time
from typing import Any, List, Optional

import uvicorn

__all__ = ["BalancedServer", "balanced", "run_balanced"]

#: How often a worker publishes its load and decides to listen or not.
TICK_S = 0.02
#: A worker silent this long is dead or hung: left out of the minimum.
STALE_S = 1.0
_FIELDS = 3  # pid, open connections, heartbeat (monotonic seconds)


def balanced() -> bool:
    return os.environ.get("OPERONX_SERVE_BALANCE", "").lower() not in ("off", "0", "false")


class BalancedServer(uvicorn.Server):
    """A uvicorn worker that listens only while it is among the least loaded."""

    def __init__(self, config: uvicorn.Config, table: Any) -> None:
        super().__init__(config)
        self._table = table  # multiprocessing Array('d', workers * _FIELDS)
        self._slot: Optional[int] = None
        self._shared: List[socket.socket] = []
        self._listening = True
        self._task: Optional[asyncio.Task] = None

    def __getstate__(self) -> dict:  # what a spawned worker receives
        state = self.__dict__.copy()
        state["_task"] = None
        return state

    async def startup(self, sockets: Optional[List[socket.socket]] = None) -> None:
        self._shared = list(sockets or [])
        await super().startup(sockets=self._copies())
        if self.should_exit or not self._shared:
            return
        self._slot = self._claim()
        self._task = asyncio.get_running_loop().create_task(self._balance())

    async def shutdown(self, sockets: Optional[List[socket.socket]] = None) -> None:
        if self._task is not None:
            self._task.cancel()
        if self._slot is not None:
            with self._table.get_lock():
                self._table[self._slot * _FIELDS] = 0
        await super().shutdown(sockets=sockets)

    # -- the listening socket ---------------------------------------------

    def _copies(self) -> List[socket.socket]:
        """This worker's own copies of the shared sockets: closing one stops
        this worker accepting, and leaves the others listening."""
        return [socket.socket(fileno=os.dup(s.fileno())) for s in self._shared]

    def _protocol(self, _loop: Any = None) -> asyncio.Protocol:
        """What uvicorn's `startup` builds for each connection."""
        config = self.config
        try:
            return config.http_protocol_class(  # type: ignore[call-arg]
                config=config,
                server_state=self.server_state,
                app_state=self.lifespan.state,
                _loop=_loop,
            )
        except TypeError:  # an uvicorn whose protocols take no `_loop`
            return config.http_protocol_class(  # type: ignore[call-arg]
                config=config, server_state=self.server_state, app_state=self.lifespan.state
            )

    def _pause(self) -> None:
        for server in self.servers:
            server.close()  # stops accepting; its connections go on
        self.servers = []
        self._listening = False

    async def _resume(self) -> None:
        loop = asyncio.get_running_loop()
        self.servers = [
            await loop.create_server(
                self._protocol, sock=sock, ssl=self.config.ssl, backlog=self.config.backlog
            )
            for sock in self._copies()
        ]
        self._listening = True

    # -- the shared table --------------------------------------------------

    def _claim(self) -> int:
        """A free row of the table (or one whose worker went silent)."""
        now = time.monotonic()
        rows = len(self._table) // _FIELDS
        with self._table.get_lock():
            for row in range(rows):
                base = row * _FIELDS
                if self._table[base] == 0 or now - self._table[base + 2] > STALE_S:
                    self._table[base : base + _FIELDS] = [os.getpid(), 0, now]
                    return row
        raise RuntimeError("serve balance: more workers than rows in the table")

    def _fewest(self, now: float) -> float:
        rows = len(self._table) // _FIELDS
        least = float("inf")
        for row in range(rows):
            base = row * _FIELDS
            if self._table[base] and now - self._table[base + 2] <= STALE_S:
                least = min(least, self._table[base + 1])
        return least

    async def _balance(self) -> None:
        base = self._slot * _FIELDS
        while not self.should_exit:
            now = time.monotonic()
            mine = len(self.server_state.connections)
            self._table[base + 1] = mine
            self._table[base + 2] = now
            listen = mine <= self._fewest(now)
            if listen and not self._listening:
                await self._resume()
            elif not listen and self._listening:
                self._pause()
            await asyncio.sleep(TICK_S)


def run_balanced(config: uvicorn.Config) -> None:
    """``uvicorn.run(..., workers=N)``, with the workers balanced."""
    import multiprocessing

    from uvicorn.supervisors import Multiprocess

    table = multiprocessing.get_context("spawn").Array("d", config.workers * _FIELDS)
    server = BalancedServer(config, table)
    sock = config.bind_socket()
    Multiprocess(config, target=server.run, sockets=[sock]).run()
