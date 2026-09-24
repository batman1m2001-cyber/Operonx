"""TritonEmbedding — tensor construction and the two input contracts.

No network. What is pinned here is the wire shape, because every way of
getting it wrong is quiet: a rank mismatch is rejected by the server with
a shape error that names neither mode, and a tokeniser applied in the
wrong mode produces a *valid* vector that simply does not match the index
it will be searched against.

Live calls against a real endpoint live in ``test_live_resources.py``.
"""

import numpy as np
import pytest

from operonx.providers.embeddings.config import EmbeddingConfig, EmbeddingType
from operonx.providers.embeddings.factory import create_embedding
from operonx.providers.embeddings.triton import TritonEmbedding
from operonx.providers.triton.dtypes import numpy_to_triton_dtype


def _config(**over):
    base = dict(
        api_type=EmbeddingType.TRITON,
        base_url="localhost:8001",
        model="bge_m3_embed",
        output_name="sentence_embedding",
        dimensions=1024,
    )
    base.update(over)
    return EmbeddingConfig(**base)


class TestConstruction:
    def test_factory_dispatch(self):
        assert isinstance(create_embedding(_config(input_name="TEXT")), TritonEmbedding)

    def test_server_side_mode_loads_no_tokenizer(self):
        """A deployment that absorbed its tokeniser should not have to ship
        one to its clients as well."""
        e = TritonEmbedding(_config(input_name="TEXT"))
        assert e.tokenizer is None
        assert e._input_name == "TEXT"

    def test_token_id_mode_requires_a_tokenizer_path(self):
        with pytest.raises(ValueError, match="tokenizer_path"):
            TritonEmbedding(_config())

    def test_base_url_required(self):
        with pytest.raises(ValueError, match="base_url"):
            TritonEmbedding(_config(base_url=None, input_name="TEXT"))

    def test_model_required(self):
        with pytest.raises(ValueError, match="model"):
            TritonEmbedding(_config(model=None, input_name="TEXT"))

    def test_output_name_defaults_to_the_pooled_output(self):
        e = TritonEmbedding(_config(output_name=None, input_name="TEXT"))
        assert e._output_name == "sentence_embedding"

    def test_ssl_is_recorded(self):
        """TLS is selected by the flag, not by a scheme on base_url."""
        assert TritonEmbedding(_config(input_name="TEXT", ssl=True))._ssl is True
        assert TritonEmbedding(_config(input_name="TEXT"))._ssl is False


class TestTextTensor:
    def test_shape_is_n_by_one(self):
        """``(n, 1)``, not ``(n,)`` — the model declares one string per
        row and rejects a flat vector as a rank mismatch."""
        out = TritonEmbedding._text_batch(["a", "b", "c"])
        assert out.shape == (3, 1)

    def test_values_are_utf8_bytes(self):
        """Explicit utf-8 rather than letting numpy pick — Vietnamese is
        exactly where that choice shows up."""
        out = TritonEmbedding._text_batch(["xin chào"])
        assert out[0][0] == "xin chào".encode("utf-8")
        assert isinstance(out[0][0], bytes)

    def test_maps_to_the_bytes_triton_dtype(self):
        out = TritonEmbedding._text_batch(["a"])
        assert numpy_to_triton_dtype(out) == "BYTES"


class TestTokenIdTensors:
    class _FakeEncoding:
        def __init__(self, ids):
            self.ids = ids

    class _FakeTokenizer:
        def __init__(self, lengths):
            self._lengths = lengths

        def encode_batch(self, texts):
            return [TestTokenIdTensors._FakeEncoding(list(range(1, n + 1))) for n in self._lengths]

        def enable_truncation(self, max_length):
            pass

    def _embedder(self, lengths, max_length=512):
        e = TritonEmbedding(_config(input_name="TEXT"))  # skips tokenizer load
        e._input_name = None
        e._max_length = max_length
        e.tokenizer = self._FakeTokenizer(lengths)
        return e

    def test_rectangular_int64_tensors(self):
        ids, mask = self._embedder([3, 5])._encode_batch(["a", "b"])
        assert ids.shape == mask.shape == (2, 5)
        assert ids.dtype == np.int64 and mask.dtype == np.int64

    def test_width_is_longest_in_batch(self):
        ids, _ = self._embedder([2, 7, 4])._encode_batch(["a", "b", "c"])
        assert ids.shape[1] == 7

    def test_mask_marks_padding(self):
        """Pad columns carry mask 0, which is what makes batch width
        irrelevant to the result for a masked model."""
        _, mask = self._embedder([2, 5])._encode_batch(["a", "b"])
        assert mask[0].tolist() == [1, 1, 0, 0, 0]
        assert mask[1].tolist() == [1, 1, 1, 1, 1]

    def test_truncates_at_max_length(self):
        ids, mask = self._embedder([100], max_length=8)._encode_batch(["a"])
        assert ids.shape == (1, 8)
        assert mask.sum() == 8

    def test_zero_width_floor(self):
        """A tokeniser emitting no special tokens turns empty strings into
        a zero-width tensor, which Triton rejects."""
        ids, mask = self._embedder([0, 0])._encode_batch(["", ""])
        assert ids.shape == (2, 1)
        assert mask.sum() == 0

    def test_maps_to_int64(self):
        ids, _ = self._embedder([3])._encode_batch(["a"])
        assert numpy_to_triton_dtype(ids) == "INT64"


class TestNormalise:
    def test_rows_become_unit_length(self):
        out = TritonEmbedding._normalize(np.array([[3.0, 4.0], [1.0, 0.0]]))
        assert np.allclose(np.linalg.norm(out, axis=1), [1.0, 1.0])

    def test_zero_vector_does_not_divide_by_zero(self):
        out = TritonEmbedding._normalize(np.array([[0.0, 0.0]]))
        assert np.isfinite(out).all()


class TestOutputDim:
    def test_falls_back_to_configured_dimensions(self):
        assert TritonEmbedding(_config(input_name="TEXT")).get_output_dim() == 1024

    def test_raises_when_unknown(self):
        e = TritonEmbedding(_config(input_name="TEXT", dimensions=None))
        with pytest.raises(RuntimeError, match="dimensions="):
            e.get_output_dim()

    def test_prefers_what_the_server_returned(self):
        e = TritonEmbedding(_config(input_name="TEXT"))
        e._output_dim = 768
        assert e.get_output_dim() == 768


class TestRunValidation:
    @pytest.mark.asyncio
    async def test_empty_list_short_circuits(self):
        e = TritonEmbedding(_config(input_name="TEXT"))
        assert await e.run([]) == {"embeddings": []}

    @pytest.mark.asyncio
    @pytest.mark.parametrize("bad", [42, [1, 2], [{"a": 1}], None])
    async def test_rejects_non_text(self, bad):
        e = TritonEmbedding(_config(input_name="TEXT"))
        with pytest.raises(ValueError, match="string or list of strings"):
            await e.run(bad)


class TestClientPooling:
    def test_ssl_is_part_of_the_cache_key(self):
        """A plaintext and a TLS channel to the same host:port are
        different connections; sharing one fails at handshake time."""
        from operonx.providers.triton import client as client_mod

        created = []

        class _Fake:
            def __init__(self, url, ssl=False):
                created.append((url, ssl))
                self.url, self.ssl = url, ssl

        original, cache = client_mod.TritonClient, dict(client_mod._clients)
        client_mod._clients.clear()
        try:
            client_mod.TritonClient = type(
                "P", (original,), {"__init__": _Fake.__init__, "__slots__": ()}
            )
            a = client_mod.TritonClient.get("h:443", ssl=True)
            b = client_mod.TritonClient.get("h:443", ssl=True)
            c = client_mod.TritonClient.get("h:443", ssl=False)
            assert a is b
            assert a is not c
            assert created == [("h:443", True), ("h:443", False)]
        finally:
            client_mod.TritonClient = original
            client_mod._clients.clear()
            client_mod._clients.update(cache)
