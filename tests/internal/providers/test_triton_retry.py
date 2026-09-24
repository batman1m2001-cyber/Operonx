"""Transient Triton failures get another attempt; refusals do not.

The failure this exists for is not a slow server. Measured against a
deployed bge-m3: a full batch of sentences at five-way concurrency
answers in 5.5s against a 30s deadline. A deadline expires anyway when
the budget goes somewhere other than inference — the TLS handshake on a
cold channel, or a response that arrives while the event loop is busy.
Both succeed on the next attempt, and a longer timeout cannot help,
because the time was never spent on the model.

So the classification matters as much as the retry: an INVALID_ARGUMENT
retried three times is three full deadlines spent on an answer that was
never coming.
"""

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from operonx.providers.triton import client as client_mod
from operonx.providers.triton.client import _is_transient, _status_code


class _Err(Exception):
    """A tritonclient error: the status code lives in the message."""


class _CodedErr(Exception):
    """A grpc error: the status code is an accessor."""

    class _Code:
        def __init__(self, name):
            self.name = name

    def __init__(self, name):
        super().__init__(name)
        self._code = self._Code(name)

    def code(self):
        return self._code


class TestClassification:
    @pytest.mark.parametrize(
        "code",
        ["DEADLINE_EXCEEDED", "UNAVAILABLE", "RESOURCE_EXHAUSTED", "ABORTED", "INTERNAL"],
    )
    def test_transient_codes_are_retried(self, code):
        assert _is_transient(_Err(f"[StatusCode.{code}] something")) is True

    @pytest.mark.parametrize(
        "code", ["INVALID_ARGUMENT", "NOT_FOUND", "UNIMPLEMENTED", "PERMISSION_DENIED"]
    )
    def test_refusals_are_not_retried(self, code):
        """The server understood and said no. Asking again wastes a deadline."""
        assert _is_transient(_Err(f"[StatusCode.{code}] nope")) is False

    def test_the_real_message_shape_is_recognised(self):
        real = (
            "Triton inference failed for model 'bge_m3_embed': "
            "[StatusCode.DEADLINE_EXCEEDED] Deadline Exceeded"
        )
        assert _status_code(_Err(real)) == "DEADLINE_EXCEEDED"
        assert _is_transient(_Err(real)) is True

    def test_an_accessor_is_preferred_over_the_message(self):
        assert _status_code(_CodedErr("UNAVAILABLE")) == "UNAVAILABLE"

    def test_an_unclassifiable_error_is_not_retried(self):
        """Unknown means unknown. A retry that cannot help still costs a timeout."""
        assert _is_transient(ValueError("boom")) is False
        assert _status_code(ValueError("boom")) is None


class _FakeRaw:
    """Fails `fail_times` with `error`, then answers."""

    def __init__(self, fail_times, error):
        self.calls = 0
        self._fail_times = fail_times
        self._error = error

    async def infer(self, **kwargs):
        self.calls += 1
        if self.calls <= self._fail_times:
            raise self._error

        class _Result:
            @staticmethod
            def as_numpy(name):
                return [[0.1, 0.2]]

        return _Result()


def _client(raw):
    c = client_mod.TritonClient.__new__(client_mod.TritonClient)
    c.url, c.ssl, c._raw = "h:1", False, raw
    return c


@pytest.fixture(autouse=True)
def _mock_grpc():
    """`infer` builds InferInput objects up front, so the module must exist.

    Patched rather than skipped: the retry loop is pure control flow and
    is worth testing on a machine without tritonclient installed.
    """
    fake = MagicMock()
    fake.InferInput = MagicMock(side_effect=lambda name, shape, dtype: MagicMock())
    fake.InferRequestedOutput = MagicMock(side_effect=lambda name: MagicMock(name=name))
    with patch.object(client_mod, "_aio_grpcclient", fake):
        yield fake


@pytest.fixture(autouse=True)
def _no_sleeping(monkeypatch):
    """Backoff is correctness elsewhere; here it is only slowness."""

    async def _instant(_seconds):
        return None

    monkeypatch.setattr(asyncio, "sleep", _instant)


class TestRetry:
    async def test_a_deadline_recovers_on_the_second_attempt(self):
        raw = _FakeRaw(1, _Err("[StatusCode.DEADLINE_EXCEEDED] Deadline Exceeded"))
        out = await _client(raw).infer(
            model="bge_m3_embed", inputs={"TEXT": [["x"]]}, outputs=["v"], decode=False
        )
        assert raw.calls == 2
        assert out["v"] is not None

    async def test_retries_are_bounded_and_the_last_error_surfaces(self):
        raw = _FakeRaw(99, _Err("[StatusCode.UNAVAILABLE] gone"))
        with pytest.raises(_Err):
            await _client(raw).infer(
                model="m",
                inputs={"TEXT": [["x"]]},
                outputs=["v"],
                retries=2,
                decode=False,
            )
        assert raw.calls == 3, "one initial attempt plus two retries"

    async def test_a_refusal_is_not_retried(self):
        raw = _FakeRaw(99, _Err("[StatusCode.INVALID_ARGUMENT] bad shape"))
        with pytest.raises(_Err):
            await _client(raw).infer(
                model="m",
                inputs={"TEXT": [["x"]]},
                outputs=["v"],
                retries=5,
                decode=False,
            )
        assert raw.calls == 1, "a refusal must cost exactly one attempt"

    async def test_retries_zero_restores_the_old_behaviour(self):
        raw = _FakeRaw(99, _Err("[StatusCode.DEADLINE_EXCEEDED] x"))
        with pytest.raises(_Err):
            await _client(raw).infer(
                model="m",
                inputs={"TEXT": [["x"]]},
                outputs=["v"],
                retries=0,
                decode=False,
            )
        assert raw.calls == 1

    async def test_a_first_attempt_that_works_costs_nothing(self):
        raw = _FakeRaw(0, _Err("unused"))
        await _client(raw).infer(model="m", inputs={"TEXT": [["x"]]}, outputs=["v"], decode=False)
        assert raw.calls == 1


class TestEmbedderWiring:
    """The config has to reach the client, or none of the above fires."""

    def test_the_embedder_reads_its_transport_knobs_from_the_resource(self):
        from operonx.providers.embeddings.config import EmbeddingConfig, EmbeddingType
        from operonx.providers.embeddings.triton import TritonEmbedding

        conf = EmbeddingConfig(
            api_type=EmbeddingType.TRITON,
            base_url="h:1",
            model="bge_m3_embed",
            input_name="TEXT",
            timeout=12.5,
            max_retries=4,
            retry_base_delay=0.25,
            retry_max_delay=3.0,
        )
        emb = TritonEmbedding(conf)
        assert emb._timeout == 12.5
        assert emb._retries == 4
        assert emb._retry_base_delay == 0.25
        assert emb._retry_max_delay == 3.0

    def test_the_defaults_are_the_documented_ones(self):
        from operonx.providers.embeddings.config import EmbeddingConfig, EmbeddingType
        from operonx.providers.embeddings.triton import TritonEmbedding

        emb = TritonEmbedding(
            EmbeddingConfig(
                api_type=EmbeddingType.TRITON,
                base_url="h:1",
                model="m",
                input_name="TEXT",
            )
        )
        assert emb._timeout == 30.0
        assert emb._retries == 2
