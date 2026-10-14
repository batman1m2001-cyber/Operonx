"""A pooled listener's connections spread evenly over its workers
(docs/CCU_CAPACITY_PLAN.md, phase 4).

Real processes: a project served with ``workers=4``, and 12 keep-alive
connections opened one after another and held, as calls are. Each worker
answers with its pid. Without balancing the kernel gives a connection to
whichever worker wakes first: a callbot load test put 10 of 20 calls on
one worker and none on another.
"""

from __future__ import annotations

import collections
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.slow

MAIN = """
import os
from operonx.app import Application, Service, http
from operonx.core import END, START, graph, op


@op
def which() -> dict:
    return {"pid": os.getpid()}


@graph
def worker():
    w = which()
    START >> w >> END


APP = Application(
    "spread",
    services=[Service("pid", http("GET", "/pid", port=PORT, workers=4), graph=worker)],
    resources=None,
    src=["."],
)
"""

MANIFEST = """
[project]
name = "spread"
src = ["."]
app = "main:APP"
"""


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _ask(port: int) -> tuple:
    """A connection held open (keep-alive), and the pid that answered it."""
    sock = socket.create_connection(("127.0.0.1", port), timeout=10)
    sock.sendall(b"GET /pid HTTP/1.1\r\nHost: x\r\nConnection: keep-alive\r\n\r\n")
    data = b""
    while b"\r\n\r\n" not in data or not data.rstrip().endswith(b"}"):
        chunk = sock.recv(4096)
        if not chunk:
            break
        data += chunk
    body = data.split(b"\r\n\r\n", 1)[1]
    return sock, json.loads(body[body.index(b"{") :])["pid"]


def _serve(root: Path, port: int, **env) -> subprocess.Popen:
    code = f"from operonx.app import Application; Application.find({str(root)!r}).serve()"
    proc = subprocess.Popen(
        [sys.executable, "-c", code],
        cwd=root,
        env={**os.environ, **env},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    deadline = time.time() + 60
    while time.time() < deadline:
        try:
            socket.create_connection(("127.0.0.1", port), timeout=1).close()
            time.sleep(3)  # every worker up
            return proc
        except OSError:
            time.sleep(0.2)
    proc.kill()
    raise AssertionError("the server did not come up")


def test_held_connections_spread_evenly_over_the_workers(tmp_path: Path):
    port = _free_port()
    root = tmp_path / "spread"
    root.mkdir()
    (root / "main.py").write_text(MAIN.replace("PORT", str(port)), encoding="utf-8")
    (root / "operonx.toml").write_text(MANIFEST, encoding="utf-8")
    proc = _serve(root, port)
    held = []
    try:
        for _ in range(12):
            sock, pid = _ask(port)
            held.append((sock, pid))
            time.sleep(0.05)  # calls come one after another
        per_worker = collections.Counter(pid for _, pid in held)
        assert len(per_worker) == 4, per_worker
        assert max(per_worker.values()) - min(per_worker.values()) <= 1, per_worker
    finally:
        for sock, _ in held:
            sock.close()
        proc.terminate()
        proc.wait(timeout=15)
