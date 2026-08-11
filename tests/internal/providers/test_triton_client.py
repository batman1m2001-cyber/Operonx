"""Tests for the low-level Triton helpers (`operonx.providers.triton`).

These cover the pure dtype/decode functions plus the ``TritonClient``
wrapper with a mocked gRPC layer — no Triton server required.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

from operonx.providers.triton import (
    DTYPE_MAP,
    decode_infer_output,
    is_text_dtype,
    numpy_to_triton_dtype,
    to_infer_array,
)

# The providers conftest auto-marks this directory as `integration`, which
# CI's `-m "not integration"` selector excludes. These tests are mock-only
# (no Triton server) and cheap, so opt back in via the `unit` marker the
# conftest honours — otherwise they never run on a PR.
pytestmark = pytest.mark.unit

# =============================================================================
# dtypes
# =============================================================================


class TestNumpyToTritonDtype:
    @pytest.mark.parametrize(
        "np_dtype,expected",
        [
            (np.float32, "FP32"),
            (np.float64, "FP64"),
            (np.float16, "FP16"),
            (np.int32, "INT32"),
            (np.int64, "INT64"),
            (np.int16, "INT16"),
            (np.int8, "INT8"),
            (np.uint8, "UINT8"),
            (np.bool_, "BOOL"),
        ],
    )
    def test_every_mapped_dtype(self, np_dtype, expected):
        arr = np.zeros(3, dtype=np_dtype)
        assert numpy_to_triton_dtype(arr) == expected

    def test_map_covers_all_parametrized_cases(self):
        # Guards against DTYPE_MAP growing without a matching test row.
        assert len(DTYPE_MAP) == 9

    def test_unsupported_dtype_raises(self):
        arr = np.zeros(3, dtype=np.complex128)
        with pytest.raises(ValueError, match="Unsupported numpy dtype"):
            numpy_to_triton_dtype(arr)


class TestToInferArray:
    def test_list_becomes_array(self):
        out = to_infer_array([1.0, 2.0, 3.0])
        assert isinstance(out, np.ndarray)
        assert out.shape == (3,)

    def test_scalar_promoted_to_1d(self):
        out = to_infer_array(5)
        assert out.ndim == 1
        assert out.shape == (1,)

    def test_zero_d_array_promoted_to_1d(self):
        out = to_infer_array(np.array(7.0))
        assert out.ndim == 1
        assert out.shape == (1,)

    def test_existing_array_passes_through_unchanged(self):
        arr = np.ones((2, 4), dtype=np.float32)
        out = to_infer_array(arr)
        assert out is arr

    def test_nested_list_keeps_shape(self):
        out = to_infer_array([[1, 2], [3, 4]])
        assert out.shape == (2, 2)


# =============================================================================
# decode
# =============================================================================


class TestIsTextDtype:
    def test_bytes_is_text(self):
        assert is_text_dtype(np.array([b"hi"]))

    def test_unicode_is_text(self):
        assert is_text_dtype(np.array(["hi"]))

    def test_object_is_text(self):
        assert is_text_dtype(np.array([object()], dtype=object))

    def test_float_is_not_text(self):
        assert not is_text_dtype(np.array([1.0], dtype=np.float32))


class TestDecodeInferOutput:
    def test_numeric_passes_through_as_array(self):
        arr = np.array([1.0, 2.0], dtype=np.float32)
        out = decode_infer_output(arr)
        assert out is arr

    def test_single_bytes_element_collapses_to_str(self):
        out = decode_infer_output(np.array([b"hello world"]))
        assert out == "hello world"

    def test_multi_bytes_elements_become_list(self):
        out = decode_infer_output(np.array([b"a", b"b", b"c"]))
        assert out == ["a", "b", "c"]

    def test_zero_d_bytes_decodes_to_str(self):
        out = decode_infer_output(np.array(b"scalar", dtype="S6"))
        assert out == "scalar"

    def test_zero_d_unicode_decodes_to_str(self):
        out = decode_infer_output(np.array("scalar"))
        assert out == "scalar"

    def test_non_bytes_object_stringified(self):
        out = decode_infer_output(np.array([123], dtype=object))
        assert out == "123"

    def test_multidim_text_flattens(self):
        out = decode_infer_output(np.array([[b"a", b"b"], [b"c", b"d"]]))
        assert out == ["a", "b", "c", "d"]


# =============================================================================
# TritonClient
# =============================================================================


@pytest.fixture
def mock_grpc():
    """Patch the lazily-imported tritonclient.grpc.aio module."""
    from operonx.providers.triton import client as client_mod

    fake = MagicMock()
    fake.InferInput = MagicMock(side_effect=lambda name, shape, dtype: MagicMock())
    fake.InferRequestedOutput = MagicMock(side_effect=lambda name: MagicMock(name=name))
    fake.InferenceServerClient = MagicMock(return_value=MagicMock())

    with patch.object(client_mod, "_aio_grpcclient", fake):
        client_mod._reset_client_cache()
        yield fake
        client_mod._reset_client_cache()


class TestTritonClientCache:
    def test_get_returns_same_instance_per_url(self, mock_grpc):
        from operonx.providers.triton import TritonClient

        a = TritonClient.get("localhost:8001")
        b = TritonClient.get("localhost:8001")
        assert a is b, "get() must reuse the cached client — gRPC channel reuse is load-bearing"

    def test_different_urls_get_different_clients(self, mock_grpc):
        from operonx.providers.triton import TritonClient

        a = TritonClient.get("localhost:8001")
        b = TritonClient.get("localhost:9001")
        assert a is not b

    def test_underlying_grpc_client_built_once_per_url(self, mock_grpc):
        from operonx.providers.triton import TritonClient

        TritonClient.get("localhost:8001")
        TritonClient.get("localhost:8001")
        TritonClient.get("localhost:8001")
        assert mock_grpc.InferenceServerClient.call_count == 1

    def test_raw_exposes_underlying_client(self, mock_grpc):
        from operonx.providers.triton import TritonClient

        c = TritonClient.get("localhost:8001")
        assert c.raw is mock_grpc.InferenceServerClient.return_value


def _mock_result(outputs: dict):
    """Build a mock InferResult whose as_numpy returns `outputs[name]`."""
    result = MagicMock()
    result.as_numpy = MagicMock(side_effect=lambda name: outputs[name])
    return result


class TestTritonClientInfer:
    @pytest.mark.asyncio
    async def test_infer_returns_decoded_outputs(self, mock_grpc):
        from operonx.providers.triton import TritonClient

        c = TritonClient.get("localhost:8001")
        c.raw.infer = AsyncMock(
            return_value=_mock_result(
                {
                    "TRANSCRIPT": np.array([b"hello there"]),
                    "EMBEDDING": np.ones(4, dtype=np.float32),
                }
            )
        )

        out = await c.infer(
            model="asr",
            inputs={"AUDIO": np.zeros(16, dtype=np.float32)},
            outputs=["TRANSCRIPT", "EMBEDDING"],
        )

        assert out["TRANSCRIPT"] == "hello there"
        assert isinstance(out["EMBEDDING"], np.ndarray)
        assert out["EMBEDDING"].shape == (4,)

    @pytest.mark.asyncio
    async def test_none_inputs_are_skipped(self, mock_grpc):
        from operonx.providers.triton import TritonClient

        c = TritonClient.get("localhost:8001")
        c.raw.infer = AsyncMock(return_value=_mock_result({"OUT": np.zeros(1)}))

        await c.infer(
            model="m",
            inputs={"A": np.zeros(2, dtype=np.float32), "B": None},
            outputs=["OUT"],
        )

        # Only "A" became an InferInput; "B" was skipped.
        assert mock_grpc.InferInput.call_count == 1
        assert mock_grpc.InferInput.call_args[0][0] == "A"

    @pytest.mark.asyncio
    async def test_inputs_are_coerced_and_dtype_mapped(self, mock_grpc):
        from operonx.providers.triton import TritonClient

        c = TritonClient.get("localhost:8001")
        c.raw.infer = AsyncMock(return_value=_mock_result({"OUT": np.zeros(1)}))

        await c.infer(model="m", inputs={"A": [1.5, 2.5]}, outputs=["OUT"])

        name, shape, dtype = mock_grpc.InferInput.call_args[0]
        assert name == "A"
        assert shape == [2]
        assert dtype == "FP64"  # python floats → float64

    @pytest.mark.asyncio
    async def test_decode_false_returns_raw_arrays(self, mock_grpc):
        from operonx.providers.triton import TritonClient

        c = TritonClient.get("localhost:8001")
        raw_text = np.array([b"hello"])
        c.raw.infer = AsyncMock(return_value=_mock_result({"TRANSCRIPT": raw_text}))

        out = await c.infer(model="m", inputs={}, outputs=["TRANSCRIPT"], decode=False)
        assert out["TRANSCRIPT"] is raw_text

    @pytest.mark.asyncio
    async def test_failed_output_becomes_none_without_failing_request(self, mock_grpc):
        from operonx.providers.triton import TritonClient

        c = TritonClient.get("localhost:8001")
        result = MagicMock()

        def _as_numpy(name):
            if name == "BAD":
                raise RuntimeError("no such output")
            return np.array([b"ok"])

        result.as_numpy = MagicMock(side_effect=_as_numpy)
        c.raw.infer = AsyncMock(return_value=result)

        out = await c.infer(model="m", inputs={}, outputs=["GOOD", "BAD"])
        assert out["GOOD"] == "ok"
        assert out["BAD"] is None

    @pytest.mark.asyncio
    async def test_inference_error_propagates(self, mock_grpc):
        from operonx.providers.triton import TritonClient

        c = TritonClient.get("localhost:8001")
        c.raw.infer = AsyncMock(side_effect=RuntimeError("triton down"))

        with pytest.raises(RuntimeError, match="triton down"):
            await c.infer(model="m", inputs={}, outputs=["OUT"])

    @pytest.mark.asyncio
    async def test_model_version_and_timeout_forwarded(self, mock_grpc):
        from operonx.providers.triton import TritonClient

        c = TritonClient.get("localhost:8001")
        c.raw.infer = AsyncMock(return_value=_mock_result({"OUT": np.zeros(1)}))

        await c.infer(
            model="m",
            inputs={},
            outputs=["OUT"],
            model_version="3",
            timeout=12.5,
        )

        kwargs = c.raw.infer.call_args.kwargs
        assert kwargs["model_name"] == "m"
        assert kwargs["model_version"] == "3"
        assert kwargs["client_timeout"] == 12.5


# =============================================================================
# The ops removed in 1.2.0 stay removed
# =============================================================================


class TestRemovedOps:
    """`OnnxOp` and `TritonOp` named their transport rather than a
    semantic. They are gone; these assert they don't quietly return."""

    def test_tritonop_is_gone(self):
        import operonx.providers as providers
        import operonx.providers.ops as ops

        with pytest.raises(AttributeError):
            ops.TritonOp
        with pytest.raises(AttributeError):
            providers.TritonOp

    def test_onnxop_is_gone(self):
        import operonx.providers as providers
        import operonx.providers.ops as ops

        with pytest.raises(AttributeError):
            ops.OnnxOp
        with pytest.raises(AttributeError):
            providers.OnnxOp

    def test_modules_are_gone(self):
        import importlib

        for mod in ("operonx.providers.ops.triton", "operonx.providers.ops.onnx"):
            with pytest.raises(ModuleNotFoundError):
                importlib.import_module(mod)

    def test_replacement_is_importable(self):
        # The whole point: the useful part survives as a helper users
        # wrap in their own @op.
        from operonx.providers.triton import TritonClient

        assert hasattr(TritonClient, "get")
        assert hasattr(TritonClient, "infer")

    def test_onnx_helper_survives_at_its_real_path(self):
        # Deprecation warnings in 1.1.0 pointed here; it must still hold.
        from operonx.providers._utils.onnx import load_onnx_session

        assert callable(load_onnx_session)

    def test_onnx_remains_a_backend(self):
        # Removing the op must not remove ONNX as an embedding/rerank backend.
        from operonx.providers.embeddings.config import EmbeddingType
        from operonx.providers.rerankers.config import RerankingType

        assert EmbeddingType.ONNX.value == "onnx"
        assert RerankingType.ONNX.value == "onnx"
