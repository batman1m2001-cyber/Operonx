"""C1 and C2: the checks that need the graphs actually built.

* **C1**: the application loads (``operonx.toml`` points at it) and every
  graph it names compiles.
* **C2**: building is pure and cheap: no model load, no socket, no API call,
  so a tool can rebuild a project on every keystroke and on a laptop with no
  network.

The network is blocked outright rather than timed: a build that reaches the
network on a fast connection looks cheap and is not. Blocking names the
address, so the offending call is findable. Slowness is reported, never
failed: a threshold that fails a build on a loaded CI box is a flaky test.
"""

from __future__ import annotations

import os
import re
import socket
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, List, Optional, Tuple

__all__ = ["BuildReport", "NetworkAttempt", "SLOW_BUILD_SECONDS", "check_build", "no_network"]

SLOW_BUILD_SECONDS = 1.0

# loopback is not "the network" here: a local test double is not what C2 is about
_LOCAL = {"127.0.0.1", "::1", "localhost", "0.0.0.0"}


@dataclass(frozen=True)
class NetworkAttempt:
    """One outbound call made while a graph was being built."""

    host: str
    port: Optional[int] = None
    via: str = "connect"

    def __str__(self) -> str:
        where = f"{self.host}:{self.port}" if self.port else self.host
        return f"{where} (via {self.via})"


@dataclass
class BuildReport:
    """What happened when one graph was built."""

    graph: str
    ok: bool = False
    seconds: float = 0.0
    error: Optional[str] = None
    network: List[NetworkAttempt] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return self.ok and not self.network

    @property
    def slow(self) -> bool:
        """Reported, never failed. A cached load is paid once by whichever
        graph builds first, so the *first* number is the honest one."""
        return self.ok and self.seconds > SLOW_BUILD_SECONDS


def _host_of(address: Any) -> Tuple[str, Optional[int]]:
    if isinstance(address, tuple) and address:
        port = address[1] if len(address) > 1 and isinstance(address[1], int) else None
        return str(address[0]), port
    return str(address), None


@contextmanager
def no_network(record: List[NetworkAttempt]) -> Iterator[None]:
    """Refuse outbound connections, recording where each wanted to go
    (``getaddrinfo`` too: a build that only resolves a name has still made
    the network a build-time dependency)."""
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex
    real_create = socket.create_connection
    real_getaddrinfo = socket.getaddrinfo

    def _blocked(address: Any, via: str) -> None:
        host, port = _host_of(address)
        if host in _LOCAL:
            return
        attempt = NetworkAttempt(host=host, port=port, via=via)
        record.append(attempt)
        raise OSError(f"C2: build-time network access blocked: {attempt}")

    def connect(self, address):  # type: ignore[no-untyped-def]
        _blocked(address, "socket.connect")
        return real_connect(self, address)

    def connect_ex(self, address):  # type: ignore[no-untyped-def]
        _blocked(address, "socket.connect_ex")
        return real_connect_ex(self, address)

    def create_connection(address, *args, **kwargs):  # type: ignore[no-untyped-def]
        _blocked(address, "socket.create_connection")
        return real_create(address, *args, **kwargs)

    def getaddrinfo(host, port, *args, **kwargs):  # type: ignore[no-untyped-def]
        _blocked((host, port), "socket.getaddrinfo")
        return real_getaddrinfo(host, port, *args, **kwargs)

    socket.socket.connect = connect  # type: ignore[method-assign]
    socket.socket.connect_ex = connect_ex  # type: ignore[method-assign]
    socket.create_connection = create_connection  # type: ignore[assignment]
    socket.getaddrinfo = getaddrinfo  # type: ignore[assignment]
    try:
        yield
    finally:
        socket.socket.connect = real_connect  # type: ignore[method-assign]
        socket.socket.connect_ex = real_connect_ex  # type: ignore[method-assign]
        socket.create_connection = real_create  # type: ignore[assignment]
        socket.getaddrinfo = real_getaddrinfo  # type: ignore[assignment]


#: what a name `.env.example` lists, but the environment lacks, holds while building
PLACEHOLDER = "operonx-lint-placeholder"


@contextmanager
def _env_contract(root: Path) -> Iterator[List[str]]:
    """The project's ``.env`` loaded (never overriding the shell), and every
    name ``.env.example`` lists but nothing set filled with :data:`PLACEHOLDER`
    for the build only: building checks the wiring, not the credentials, and
    C12 makes sure the list is complete."""
    added: List[str] = []
    env_file = root / ".env"
    if env_file.is_file():
        try:
            from dotenv import dotenv_values

            for k, v in dotenv_values(env_file).items():
                if k not in os.environ and v is not None:
                    os.environ[k] = v
                    added.append(k)
        except ImportError:
            pass
    example = root / ".env.example"
    if example.is_file():
        for name in re.findall(
            r"^\s*#?\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=", example.read_text(), re.M
        ):
            if name not in os.environ:
                os.environ[name] = PLACEHOLDER
                added.append(name)
    try:
        yield added
    finally:
        for k in added:
            os.environ.pop(k, None)


def check_build(root: Path) -> List[BuildReport]:
    """Load the application at *root* and build every graph it names, with
    the network blocked, timing each.

    This imports the project's modules, so run it in the project's own
    environment, one project per process. A failure to load the application
    at all is one report named ``application`` (C1).
    """
    from operonx.app.application import Application

    root = Path(root).resolve()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    with _env_contract(root):
        return _build_all(Application, root)


def _build_all(Application: Any, root: Path) -> List[BuildReport]:
    attempts: List[NetworkAttempt] = []
    started = time.perf_counter()
    try:
        with no_network(attempts):
            app = Application.find(root)
            graphs = app.graphs
    except Exception as exc:  # any project failure is a finding
        return [
            BuildReport(
                graph="application",
                error=f"{type(exc).__name__}: {exc}",
                seconds=time.perf_counter() - started,
                network=attempts,
            )
        ]
    reports: List[BuildReport] = []
    for ref in graphs:
        attempts = []
        report = BuildReport(graph=ref.name)
        t0 = time.perf_counter()
        try:
            with no_network(attempts):
                ref.compile()
            report.ok = True
        except Exception as exc:  # any project failure is a finding
            report.error = f"{type(exc).__name__}: {exc}"
        report.seconds = time.perf_counter() - t0
        report.network = attempts
        reports.append(report)
    return reports
