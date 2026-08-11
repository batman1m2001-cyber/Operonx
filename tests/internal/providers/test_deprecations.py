"""Deprecation warnings for the backend-named ops removed in 2.0.0.

`OnnxOp` and `TritonOp` name their *transport* rather than a semantic,
which is the anti-pattern OP_TAXONOMY_REFACTOR_PLAN.md exists to remove.
They keep working through 1.1.x; these tests pin the warning contract so
the message stays actionable rather than decaying into "deprecated, good
luck".
"""

import warnings

import pytest

pytestmark = pytest.mark.unit


def _warning_from(fn):
    """Return the single DeprecationWarning raised while calling fn()."""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        fn()
    deprecations = [w for w in caught if issubclass(w.category, DeprecationWarning)]
    assert len(deprecations) == 1, f"expected exactly one DeprecationWarning, got {caught}"
    return str(deprecations[0].message)


class TestOnnxOpDeprecation:
    def test_warns(self):
        from operonx.providers.ops import OnnxOp

        msg = _warning_from(lambda: OnnxOp(name="probe", resource="sentiment"))
        assert "OnnxOp is deprecated" in msg

    def test_names_the_removal_version(self):
        from operonx.providers.ops import OnnxOp

        msg = _warning_from(lambda: OnnxOp(name="probe", resource="sentiment"))
        assert "2.0.0" in msg

    def test_points_at_the_real_helper_path(self):
        from operonx.providers.ops import OnnxOp

        msg = _warning_from(lambda: OnnxOp(name="probe", resource="sentiment"))
        # An earlier draft of the plan cited providers/onnx/backend.py, which
        # has no such function — a copy-pasted ImportError for every user.
        assert "operonx.providers._utils.onnx.load_onnx_session" in msg

    def test_describes_the_return_shape(self):
        from operonx.providers.ops import OnnxOp

        msg = _warning_from(lambda: OnnxOp(name="probe", resource="sentiment"))
        # load_onnx_session returns a 3-tuple, not a session — without this
        # the next failure is an AttributeError on .run().
        assert "(session, tokenizer, device)" in msg
        assert "tokenizer.json" in msg

    def test_mentions_onnx_survives_as_a_backend(self):
        from operonx.providers.ops import OnnxOp

        msg = _warning_from(lambda: OnnxOp(name="probe", resource="sentiment"))
        assert "EmbeddingOp" in msg and "RerankOp" in msg

    def test_still_functional(self):
        from operonx.providers.ops import OnnxOp

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            op = OnnxOp(name="probe", resource="sentiment")
        # Deprecated, not broken — 1.1.x consumers keep running.
        assert op.type == "onnx"
        assert "embeddings" in op.inputs


class TestTritonOpDeprecation:
    def _op(self):
        from operonx.providers.ops import TritonOp

        return TritonOp(
            name="stt",
            resource={"url": "localhost:8001", "model": "asr"},
            inputs_map={"AUDIO": "audio"},
            outputs_map={"TRANSCRIPT": "transcript"},
        )

    def test_warns(self):
        msg = _warning_from(self._op)
        assert "TritonOp is deprecated" in msg

    def test_names_the_removal_version(self):
        assert "2.0.0" in _warning_from(self._op)

    def test_points_at_the_pooled_client(self):
        msg = _warning_from(self._op)
        # Reusing the pooled client matters: a fresh gRPC channel per call
        # adds connection setup to every request on a real-time path.
        assert "operonx.providers.triton.TritonClient.get" in msg

    def test_mentions_vector_search_replacement(self):
        assert "VectorSearchOp" in _warning_from(self._op)

    def test_still_functional(self):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            op = self._op()
        assert op.type == "triton"
        assert op.model_name == "asr"


class TestWarningHygiene:
    def test_stacklevel_points_at_caller(self):
        """The warning must name the user's file, not operonx internals —
        otherwise it's unactionable in a large codebase."""
        from operonx.providers.ops import OnnxOp

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            OnnxOp(name="probe", resource="sentiment")
        assert caught[0].filename == __file__

    def test_no_warning_from_the_replacement_ops(self):
        """The ops users are being pointed toward must be warning-free, or
        the migration looks like a lateral move."""
        from operonx.providers.ops import DocFetchOp, VectorSearchOp

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            VectorSearchOp(name="s", resource="docs")
            DocFetchOp(name="f", resource="main")
        assert [w for w in caught if issubclass(w.category, DeprecationWarning)] == []
