"""Cached gRPC channels are closed at exit, not left to `__dealloc__`.

An unclosed aio channel is torn down during interpreter shutdown, after
``grpc_aio`` has cleared its globals, and prints under the run's own
output:

    Exception ignored in: 'grpc._cython.cygrpc.AioChannel.__dealloc__'
    AttributeError: 'NoneType' object has no attribute 'POLLER'

It changes no exit code and it alarms everyone who reads it. `atexit`
closes early enough that the path is never taken.
"""

import asyncio

import pytest

from operonx.providers.triton import client as client_mod


class _FakeRaw:
    def __init__(self, coroutine: bool):
        self.closed = 0
        self._coroutine = coroutine

    def close(self):
        if self._coroutine:

            async def _aclose():
                self.closed += 1

            return _aclose()
        self.closed += 1
        return None


class _FakeClient:
    def __init__(self, raw):
        self._raw = raw

    @property
    def raw(self):
        return self._raw


@pytest.fixture(autouse=True)
def _clean_cache():
    client_mod._clients.clear()
    yield
    client_mod._clients.clear()


def test_an_async_close_is_driven_to_completion():
    raw = _FakeRaw(coroutine=True)
    client_mod._clients[("h:1", False)] = _FakeClient(raw)
    client_mod.close_all()
    assert raw.closed == 1


def test_a_sync_close_is_called_too():
    raw = _FakeRaw(coroutine=False)
    client_mod._clients[("h:1", False)] = _FakeClient(raw)
    client_mod.close_all()
    assert raw.closed == 1


def test_the_cache_is_emptied_so_a_second_pass_is_a_no_op():
    raw = _FakeRaw(coroutine=True)
    client_mod._clients[("h:1", False)] = _FakeClient(raw)
    client_mod.close_all()
    client_mod.close_all()
    assert raw.closed == 1
    assert client_mod._clients == {}


def test_every_client_is_attempted_even_when_one_raises():
    """One bad channel must not strand the others — they print too."""

    class _Exploding:
        @property
        def raw(self):
            raise RuntimeError("channel already gone")

    good = _FakeRaw(coroutine=True)
    client_mod._clients[("bad", False)] = _Exploding()
    client_mod._clients[("good", False)] = _FakeClient(good)
    client_mod.close_all()
    assert good.closed == 1


def test_a_raw_without_close_is_skipped():
    class _NoClose:
        pass

    client_mod._clients[("h:1", False)] = _FakeClient(_NoClose())
    client_mod.close_all()  # must not raise


def test_empty_cache_is_a_no_op():
    client_mod.close_all()


async def test_close_all_is_safe_while_a_loop_is_running():
    """Called from inside a running loop, it still must not raise."""
    raw = _FakeRaw(coroutine=True)
    client_mod._clients[("h:1", False)] = _FakeClient(raw)
    client_mod.close_all()
    assert client_mod._clients == {}
    await asyncio.sleep(0)
