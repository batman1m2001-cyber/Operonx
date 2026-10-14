"""One answer to "is this error worth another try?": ``operonx.TRANSIENT``.

docs/RETRY_PLAN.md R4. An op's ``retry=``, a run's default retry, the LLM
transport and Triton all ask it, so a project that adds its own error
(``TRANSIENT.also``, ``[retry] transient``) adds it to all of them.
"""

import sys
import types

import httpx
import openai
import pytest

from operonx import END, START, TRANSIENT, Operon, Retry, graph, op
from operonx.providers.llms.config import LLMType, OpenAIConfig
from operonx.providers.ops.llm import LLMOp
from operonx.providers.triton.client import _is_transient

pytestmark = pytest.mark.unit


def _llm():
    config = OpenAIConfig(
        api_type=LLMType.OPENAI,
        api_key="k",
        base_url="http://localhost",
        model="m",
        max_retries=3,
        retry_base_delay=0.001,
        retry_max_delay=0.002,
    )
    return types.SimpleNamespace(config=config)


class _Op(LLMOp):
    """LLMOp with construction skipped: only the transport retry runs."""

    def __init__(self):  # noqa: D107
        self.name = "test_op"


class Throttled(Exception):
    """A CRM's own "slow down", which carries no HTTP status."""


@pytest.fixture(autouse=True)
def _restore():
    kept = TRANSIENT._types, TRANSIENT._names
    yield
    TRANSIENT._types, TRANSIENT._names = kept


class _Grpc(Exception):
    def __init__(self, name):
        super().__init__(name)
        self._name = name

    def code(self):
        return types.SimpleNamespace(name=self._name)


@pytest.mark.parametrize(
    "error, worth",
    [
        (TimeoutError(), True),
        (ConnectionResetError(), True),
        (_Grpc("UNAVAILABLE"), True),
        (Exception("[StatusCode.DEADLINE_EXCEEDED] Deadline Exceeded"), True),
        (_Grpc("INVALID_ARGUMENT"), False),
        (
            openai.APIStatusError(
                "x",
                response=httpx.Response(503, request=httpx.Request("GET", "http://h")),
                body=None,
            ),
            True,
        ),
        (
            openai.APIStatusError(
                "x",
                response=httpx.Response(400, request=httpx.Request("GET", "http://h")),
                body=None,
            ),
            False,
        ),
        (ValueError("not a number"), False),
        (Throttled(), False),
    ],
)
def test_the_one_list(error, worth):
    assert TRANSIENT(error) is worth
    assert _is_transient(error) is worth  # Triton asks the same


def test_a_projects_own_error_counts_once_added():
    TRANSIENT.also(Throttled)
    assert TRANSIENT(Throttled())


def test_a_name_is_imported_when_first_needed(monkeypatch):
    mod = types.ModuleType("crm_errors_fake")
    mod.Throttled = Throttled
    monkeypatch.setitem(sys.modules, "crm_errors_fake", mod)
    TRANSIENT.also("crm_errors_fake:Throttled")
    assert TRANSIENT._names  # not imported yet
    assert TRANSIENT(Throttled())
    assert not TRANSIENT._names


def test_a_bad_name_fails_the_check_and_is_never_transient():
    TRANSIENT.also("no_such_module_xyz:Throttled")
    with pytest.raises(ValueError, match="no_such_module_xyz:Throttled"):
        TRANSIENT.check()
    assert not TRANSIENT(Throttled())


def test_also_refuses_what_is_not_an_exception():
    with pytest.raises(TypeError):
        TRANSIENT.also(42)
    with pytest.raises(ValueError, match="module:Name"):
        TRANSIENT.also("Throttled")


CALLS: list = []


@op
async def crm(x: int) -> dict:
    CALLS.append(x)
    if len(CALLS) == 1:
        raise Throttled("slow down")
    return {"y": x}


@graph
def sync(x):
    c = crm(x=x)
    START >> c >> END


async def test_an_ops_retry_takes_the_projects_error():
    CALLS.clear()
    TRANSIENT.also(Throttled)
    out = await Operon(sync, params={"x": None}, retry=Retry(initial=0.001)).run({"x": 2})
    assert out["y"] == 2 and len(CALLS) == 2


async def test_the_llm_transport_takes_the_projects_error():
    TRANSIENT.also(Throttled)
    calls = []

    async def call():
        calls.append(1)
        if len(calls) == 1:
            raise Throttled("slow down")
        return "ok"

    assert await _Op()._call_with_retry(call, llm=_llm()) == "ok"
    assert len(calls) == 2


async def test_the_llm_transport_still_lets_a_bad_request_through():
    calls = []

    async def call():
        calls.append(1)
        raise ValueError("bad prompt")

    with pytest.raises(ValueError):
        await _Op()._call_with_retry(call, llm=_llm())
    assert len(calls) == 1
