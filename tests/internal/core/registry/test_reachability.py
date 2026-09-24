"""`health_check` asks whether anything answers, not just whether it built.

Constructing an HTTP or gRPC client opens no socket, so the old check
reported a hub full of corporate endpoints as healthy on a laptop with the
VPN down. The real symptom arrived later, as every call in a batch timing
out one at a time — minutes of it, with no single line saying why.

Now the check resolves the address a config points at and opens a TCP
connection. Not a request: no auth, no model, no cost. Just enough to tell
"the network cannot see this" from "the service said no", which are
different problems with different fixes.

`require_reachable` is the version that stops the flow, because a warning
nobody reads is how the minutes get wasted.
"""

from __future__ import annotations

import socket
import threading

import pytest

from operonx.core.registry.errors import ResourceUnreachable
from operonx.core.registry.shortcuts.reachability import endpoint_of, probe


class _Cfg:
    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


@pytest.fixture
def listening_port():
    """A real socket that accepts, so "reachable" is not assumed."""
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    stop = threading.Event()

    def _serve():
        srv.settimeout(0.2)
        while not stop.is_set():
            try:
                conn, _ = srv.accept()
                conn.close()
            except socket.timeout:
                continue
            except OSError:
                break

    t = threading.Thread(target=_serve, daemon=True)
    t.start()
    yield port
    stop.set()
    t.join(timeout=2)
    srv.close()


# ── reading an address out of a config ─────────────────────────────────


class TestEndpointOf:
    def test_explicit_port_wins(self):
        assert endpoint_of(_Cfg(base_url="https://example.com:8443/v1")) == (
            "example.com",
            8443,
        )

    def test_https_defaults_to_443(self):
        assert endpoint_of(_Cfg(base_url="https://example.com")) == ("example.com", 443)

    def test_http_defaults_to_80(self):
        assert endpoint_of(_Cfg(base_url="http://example.com/v1")) == ("example.com", 80)

    def test_a_bare_hostport_is_understood(self):
        """Triton configs carry `host:port` with no scheme."""
        assert endpoint_of(_Cfg(base_url="triton.internal:8001")) == (
            "triton.internal",
            8001,
        )

    def test_host_and_port_fields(self):
        assert endpoint_of(_Cfg(host="db.internal", port=5432)) == ("db.internal", 5432)

    def test_a_local_resource_has_no_address(self):
        """An in-memory or filesystem resource must not be probed.

        Returning a fabricated address would invent a failure for something
        that was never going to touch the network.
        """
        assert endpoint_of(_Cfg(kind="memory")) is None

    def test_an_empty_url_is_not_an_address(self):
        assert endpoint_of(_Cfg(base_url="")) is None

    def test_fields_are_consulted_in_order(self):
        cfg = _Cfg(base_url="https://first.example:1", url="https://second.example:2")
        assert endpoint_of(cfg) == ("first.example", 1)


# ── the probe itself ───────────────────────────────────────────────────


class TestProbe:
    def test_an_accepting_port_returns_none(self, listening_port):
        assert probe("127.0.0.1", listening_port, timeout=2.0) is None

    def test_a_closed_port_says_so(self):
        reason = probe("127.0.0.1", 9, timeout=0.5)
        assert reason and "127.0.0.1:9" in reason

    def test_an_unroutable_address_does_not_hang(self):
        """Short timeout is the feature: fail in seconds, not across a batch."""
        reason = probe("10.255.255.1", 65000, timeout=0.5)
        assert reason is not None

    def test_it_never_raises(self):
        """A probe is diagnosis; it must not become the failure it reports."""
        assert probe("this-host-does-not-exist.invalid", 443, timeout=0.5) is not None


# ── stopping the flow ──────────────────────────────────────────────────


class TestResourceUnreachable:
    def test_it_names_every_failure_not_just_the_first(self):
        """One run should tell you everything that is missing."""
        err = ResourceUnreachable({"llm:a": "no answer", "embedding:b": "refused"})
        text = str(err)
        assert "llm:a" in text and "embedding:b" in text
        assert "2 resource(s) unreachable" in text

    def test_it_says_what_to_do(self):
        err = ResourceUnreachable({"llm:a": "no answer"})
        assert "VPN" in str(err)

    def test_the_failures_stay_inspectable(self):
        """A caller that wants to branch on which one failed can."""
        err = ResourceUnreachable({"llm:a": "no answer"})
        assert err.failures == {"llm:a": "no answer"}

    def test_it_is_a_runtime_error(self):
        """Existing `except Exception` paths keep working."""
        assert issubclass(ResourceUnreachable, RuntimeError)
