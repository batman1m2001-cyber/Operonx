"""C1 and C2 — the checks that need the graph actually built.

The AST rules in ``lint`` decide everything they can from source. These two
cannot be decided that way:

* **C1** — an entry resolves and the graph constructs.
* **C2** — construction is *pure and cheap*: no model load, no socket, no
  API call. This is what lets the UI rebuild a project on every keystroke,
  and what lets extraction work on a laptop with no network.

Network access is blocked outright rather than merely timed. A build that
reaches the network on a fast connection looks cheap and is not: it will be
slow on a plane, wrong behind a proxy, and a silent dependency on a service
being up. Blocking turns "it happened to work here" into a hard answer, and
names the address so the offending call is findable.

Cheapness is reported, not enforced. A threshold that fails a build on a
loaded CI box is a flaky test, so slow builds are flagged and left for a
human to judge.
"""

from __future__ import annotations

import socket
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Iterator, List, Optional, Tuple

from operonx_project.extract import ExtractError, build_entry
from operonx_project.manifest import Manifest, ManifestError

__all__ = ["BuildReport", "NetworkAttempt", "check_build", "no_network", "SLOW_BUILD_SECONDS"]

# Three orders of magnitude above a graph that constructs nothing heavy
# (callbot's `asr_flow` builds in 3ms), so this does not fire on a loaded
# CI box — only on work that does not belong in a constructor.
SLOW_BUILD_SECONDS = 1.0

# Loopback is not "the network" for this purpose — a local test double or a
# sidecar is not the dependency C2 is about.
_LOCAL = {"127.0.0.1", "::1", "localhost", "0.0.0.0"}


@dataclass(frozen=True)
class NetworkAttempt:
    """One outbound call made while a graph was being constructed."""

    host: str
    port: Optional[int] = None
    via: str = "connect"

    def __str__(self) -> str:
        where = f"{self.host}:{self.port}" if self.port else self.host
        return f"{where} (via {self.via})"


@dataclass
class BuildReport:
    """What happened when one declared graph was built."""

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
        """Cheap enough to rebuild on every keystroke?

        Reported, never failed. Note that a cached load is paid once by
        whichever graph builds first — callbot's ONNX denoise session is a
        module-level global, so the first pipeline costs seconds and the
        rest are free. The *first* number is the honest one.
        """
        return self.ok and self.seconds > SLOW_BUILD_SECONDS


def _host_of(address: Any) -> Tuple[str, Optional[int]]:
    if isinstance(address, tuple) and address:
        port = address[1] if len(address) > 1 and isinstance(address[1], int) else None
        return str(address[0]), port
    return str(address), None


@contextmanager
def no_network(record: List[NetworkAttempt]) -> Iterator[None]:
    """Refuse outbound connections, recording where each one wanted to go.

    Patches the four entry points a client library can reach the network
    through, including ``getaddrinfo`` — a build that only resolves a name
    has still made the network a build-time dependency.
    """
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
        raise OSError(f"C2: build-time network access blocked — {attempt}")

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


def check_build(manifest: Manifest) -> List[BuildReport]:
    """Build every declared graph offline, timing each.

    One process handles one project, and this imports the project's modules,
    so a caller checking several projects must fork per project.
    """
    reports: List[BuildReport] = []
    for spec in manifest.graphs:
        attempts: List[NetworkAttempt] = []
        started = time.perf_counter()
        report = BuildReport(graph=spec.name)
        try:
            with no_network(attempts):
                build_entry(spec, manifest.root)
            report.ok = True
        except (ExtractError, ManifestError) as exc:
            report.error = str(exc)
        except Exception as exc:  # noqa: BLE001 — any project failure is a finding
            report.error = f"{type(exc).__name__}: {exc}"
        report.seconds = time.perf_counter() - started
        report.network = attempts
        reports.append(report)
    return reports
