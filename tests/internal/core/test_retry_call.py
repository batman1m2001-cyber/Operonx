"""One call retry for every resource: `retry_call`, `resource_retry` (docs/RETRY_PLAN.md R3).

An LLM, an agent's model, Triton and Gemini's token refresh retried each in a
loop of its own, with its own settings and its own idea of a transient error.
They now share one helper built on `Retry`, `TRANSIENT` and `mark_retried`.
"""

import asyncio
import time
from types import SimpleNamespace

import httpx
import pytest

from operonx import END, START, Operon, Retry, graph, op
from operonx.core.policy import _RETRIED_ATTR, resource_retry, retry_call

pytestmark = pytest.mark.unit

FAST = Retry(max_attempts=3, initial=0.001, jitter=False)


def _status(code, headers=None):
    response = httpx.Response(code, headers=headers or {}, request=httpx.Request("GET", "http://h"))
    error = Exception(f"HTTP {code}")
    error.status_code = code
    error.response = response
    return error


# -- resource_retry: one vocabulary, the old keys as aliases -------------------


def test_no_config_gives_the_kinds_default():
    assert resource_retry(None, attempts=1, initial=5.0, max_interval=60.0) == Retry(
        max_attempts=1, initial=5.0, max_interval=60.0
    )


def test_the_old_keys_are_read():
    cfg = SimpleNamespace(
        max_retries=2, retry_base_delay=1.0, retry_max_delay=8.0, retry_min_delay=0.5
    )
    assert resource_retry(cfg, attempts=1, initial=5.0, max_interval=60.0) == Retry(
        max_attempts=3, initial=1.0, max_interval=8.0, min_interval=0.5
    )


def test_a_retry_table_wins_over_the_old_keys():
    cfg = SimpleNamespace(retry={"max_attempts": 4, "initial": 0.2}, max_retries=9)
    assert resource_retry(cfg, attempts=1, initial=5.0, max_interval=60.0) == Retry(
        max_attempts=4, initial=0.2, max_interval=60.0
    )


def test_a_retry_table_names_its_mistake():
    with pytest.raises(ValueError, match="not max_retries"):
        resource_retry(
            SimpleNamespace(retry={"max_retries": 3}), attempts=1, initial=1, max_interval=1
        )


# -- Retry.delay: Retry-After, floor and cap --------------------------------------


def test_retry_after_is_honoured_and_capped():
    retry = Retry(max_interval=10.0)
    assert retry.delay(1, _status(429, {"retry-after": "7"})) == 7.0
    assert retry.delay(1, _status(429, {"retry-after-ms": "1500"})) == 1.5
    assert retry.delay(1, _status(429, {"retry-after": "3600"})) == 10.0


def test_min_interval_is_a_floor():
    retry = Retry(initial=0.01, min_interval=0.5, max_interval=1.0)
    assert all(retry.delay(n) >= 0.5 for n in range(1, 5))
    with pytest.raises(ValueError, match="min_interval"):
        Retry(min_interval=5.0, max_interval=1.0)


# -- retry_call -------------------------------------------------------------------


async def test_a_transient_error_is_retried():
    calls = []

    async def call():
        calls.append(1)
        if len(calls) < 3:
            raise _status(503)
        return "ok"

    assert await retry_call(call, FAST) == "ok"
    assert len(calls) == 3


async def test_a_bad_request_fails_at_once_and_unmarked():
    calls = []

    async def call():
        calls.append(1)
        raise _status(400)

    with pytest.raises(Exception) as caught:
        await retry_call(call, FAST)
    assert len(calls) == 1 and not getattr(caught.value, _RETRIED_ATTR, False)


async def test_given_up_after_retrying_is_marked_so_an_op_does_not_retry_it_again():
    async def call():
        raise _status(503)

    with pytest.raises(Exception) as caught:
        await retry_call(call, FAST)
    assert getattr(caught.value, _RETRIED_ATTR, False)

    with pytest.raises(Exception) as once:
        await retry_call(call, Retry(max_attempts=1))
    assert not getattr(once.value, _RETRIED_ATTR, False)  # nothing was retried


async def test_a_rejected_result_is_retried_and_the_last_one_returned():
    answers = iter(["", "", ""])

    async def call():
        return next(answers)

    assert await retry_call(call, FAST, again=lambda r: r == "") == ""


# -- inside an op, the wait gives the run's slot back (as R1) -------------------------

LOG: dict = {}


@op
async def calls_out(x: int) -> dict:
    tries = []

    async def request():
        tries.append(time.perf_counter())
        if len(tries) == 1:
            raise _status(429)
        return x

    LOG["calls"] = tries
    return {"a": await retry_call(request, Retry(max_attempts=2, initial=0.25, jitter=False))}


@op
async def healthy(x: int) -> dict:
    await asyncio.sleep(0.01)
    LOG["healthy"] = time.perf_counter()
    return {"h": x}


@graph
def side_by_side(x):
    c = calls_out(x=x)
    h = healthy(x=x)
    START >> c >> END
    START >> h >> END


async def test_a_call_retry_inside_an_op_gives_the_slot_back():
    LOG.clear()
    out = await Operon(side_by_side, params={"x": None}, max_concurrency=1).run({"x": 3})
    assert out["a"] == 3 and out["h"] == 3
    first, second = LOG["calls"]
    assert second - first >= 0.25
    assert LOG["healthy"] < second  # ran during the wait, at max_concurrency=1


# -- Gemini's token refresh: the same helper, its own errors -----------------------


async def test_gemini_token_refresh_retries_network_errors(monkeypatch):
    requests = pytest.importorskip("requests")
    gemini = pytest.importorskip("operonx.providers.llms.gemini")
    waits = []

    async def no_wait(delay):
        waits.append(delay)

    monkeypatch.setattr(asyncio, "sleep", no_wait)
    tries = []

    class Credentials:
        token = "fresh"

        def refresh(self, request):
            tries.append(1)
            if len(tries) < 3:
                raise requests.exceptions.ConnectionError("oauth2.googleapis.com unreachable")

    owner = SimpleNamespace(credentials=Credentials())
    assert await gemini.GeminiOpenAISDKModel._refresh_token(owner) == "fresh"
    assert len(tries) == 3 and waits == [1.0, 2.0]

    tries.clear()
    owner.credentials.refresh = lambda request: (_ for _ in ()).throw(
        requests.exceptions.Timeout("slow")
    )
    with pytest.raises(Exception, match="Token refresh failed after 3 attempts"):
        await gemini.GeminiOpenAISDKModel._refresh_token(owner)
