"""C1 and C2 — the checks that require actually building the graph."""

from __future__ import annotations

import itertools
import socket
from pathlib import Path

import pytest
from operonx_project.buildcheck import NetworkAttempt, check_build, no_network
from operonx_project.manifest import Manifest

pytestmark = pytest.mark.unit

_SEQ = itertools.count()

CLEAN = """
from operonx.core import END, PARENT, START, graph, op

@op
def first(x: int):
    return {"y": x + 1}

@graph
def flow(x):
    a = first(x=x)
    a["y"] >> PARENT["y"]
    START >> a >> END
"""

# Reaching the network while *constructing* — the thing C2 forbids.
PHONES_HOME = """
import socket
from operonx.core import END, PARENT, START, graph, op

@op
def first(x: int):
    return {"y": x}

@graph
def flow(x):
    socket.getaddrinfo("example.invalid", 443)
    a = first(x=x)
    a["y"] >> PARENT["y"]
    START >> a >> END
"""


def project(tmp_path: Path, source: str) -> Manifest:
    mod = f"bc_{next(_SEQ)}"
    (tmp_path / f"{mod}.py").write_text(source, encoding="utf-8")
    (tmp_path / "operonx.toml").write_text(
        f'[project]\nname="d"\n[[graph]]\nname="flow"\nentry="{mod}:flow"\n', encoding="utf-8"
    )
    return Manifest.load(tmp_path)


class TestNoNetwork:
    def test_outbound_connection_is_blocked_and_recorded(self):
        seen = []
        with no_network(seen):
            with pytest.raises(OSError, match="C2"):
                socket.create_connection(("example.invalid", 443))
        assert seen and seen[0].host == "example.invalid" and seen[0].port == 443

    def test_name_resolution_counts_as_network(self):
        """Resolving a name still makes the network a build-time dependency."""
        seen = []
        with no_network(seen):
            with pytest.raises(OSError):
                socket.getaddrinfo("example.invalid", 80)
        assert seen[0].via == "socket.getaddrinfo"

    def test_loopback_is_allowed(self):
        """A local sidecar is not the dependency C2 is about."""
        seen = []
        with no_network(seen):
            with pytest.raises(OSError):
                # Blocked by the OS (nothing listening), not by us.
                socket.create_connection(("127.0.0.1", 9), timeout=0.05)
        assert seen == []

    def test_socket_is_restored_afterwards(self):
        before = (socket.socket.connect, socket.create_connection, socket.getaddrinfo)
        with no_network([]):
            pass
        assert (socket.socket.connect, socket.create_connection, socket.getaddrinfo) == before

    def test_restored_even_when_the_body_raises(self):
        before = socket.getaddrinfo
        with pytest.raises(ValueError):
            with no_network([]):
                raise ValueError("boom")
        assert socket.getaddrinfo is before


class TestCheckBuild:
    def test_clean_project_builds_offline(self, tmp_path):
        (report,) = check_build(project(tmp_path, CLEAN))
        assert report.clean and report.ok and report.network == []

    def test_build_that_reaches_the_network_is_reported(self, tmp_path):
        (report,) = check_build(project(tmp_path, PHONES_HOME))
        assert not report.clean
        assert any(a.host == "example.invalid" for a in report.network)

    def test_unbuildable_project_is_reported_not_raised(self, tmp_path):
        (report,) = check_build(project(tmp_path, "def flow(x):\n    raise RuntimeError('no')\n"))
        assert not report.ok and "no" in (report.error or "")

    def test_duration_is_measured(self, tmp_path):
        (report,) = check_build(project(tmp_path, CLEAN))
        assert report.seconds > 0

    def test_a_fast_build_is_not_flagged_slow(self, tmp_path):
        (report,) = check_build(project(tmp_path, CLEAN))
        assert not report.slow

    def test_every_declared_graph_is_checked(self, tmp_path):
        mod = f"bc_{next(_SEQ)}"
        (tmp_path / f"{mod}.py").write_text(CLEAN, encoding="utf-8")
        (tmp_path / "operonx.toml").write_text(
            f'[project]\nname="d"\n'
            f'[[graph]]\nname="one"\nentry="{mod}:flow"\n'
            f'[[graph]]\nname="two"\nentry="{mod}:flow"\n',
            encoding="utf-8",
        )
        assert [r.graph for r in check_build(Manifest.load(tmp_path))] == ["one", "two"]


class TestAttemptFormatting:
    def test_reads_as_an_address(self):
        assert str(NetworkAttempt("api.openai.com", 443, "socket.connect")) == (
            "api.openai.com:443 (via socket.connect)"
        )

    def test_portless(self):
        assert str(NetworkAttempt("api.openai.com", None, "x")) == "api.openai.com (via x)"


class TestCli:
    def test_build_flag_reports_and_succeeds(self, tmp_path, capsys):
        from operonx_project.cli import main

        project(tmp_path, CLEAN)
        assert main([str(tmp_path), "--build"]) == 0
        assert "built offline" in capsys.readouterr().out

    def test_build_flag_fails_when_the_network_is_touched(self, tmp_path, capsys):
        from operonx_project.cli import main

        project(tmp_path, PHONES_HOME)
        assert main([str(tmp_path), "--build"]) == 1
        assert "reached the network" in capsys.readouterr().out
