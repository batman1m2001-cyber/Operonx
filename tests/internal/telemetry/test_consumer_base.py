"""Unit tests for `operonx.telemetry.consumer.Consumer` base class."""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

from operonx.core.workflow_trace import WorkflowTrace
from operonx.telemetry.consumer import Consumer


class _NoopConsumer(Consumer):
    """Trivial subclass — we only need `consume` implemented to
    instantiate; tests exercise the base utilities directly."""

    def consume(self, trace: WorkflowTrace) -> dict:
        return {"n": len(trace.nodes)}


@pytest.fixture
def consumer():
    return _NoopConsumer()


# ============================================================
# sanitize — strip non-JSON, keep everything else
# ============================================================


class TestSanitize:
    def test_scalars_pass_through(self, consumer):
        assert consumer.sanitize(1) == 1
        assert consumer.sanitize(1.5) == 1.5
        assert consumer.sanitize(True) is True
        assert consumer.sanitize(None) is None
        assert consumer.sanitize("hi") == "hi"

    def test_dict_and_list_recurse(self, consumer):
        payload = {"a": 1, "b": [2, {"c": "x"}]}
        out = consumer.sanitize(payload)
        assert out == payload
        # Not the same object — sanitize returns fresh containers.
        assert out is not payload

    def test_tuple_becomes_list(self, consumer):
        assert consumer.sanitize((1, 2, 3)) == [1, 2, 3]

    def test_unserializable_gets_marker(self, consumer):
        queue = asyncio.Queue()
        out = consumer.sanitize({"q": queue})
        assert out == {"q": {"$unserializable": "Queue"}}

    def test_function_object_stripped(self, consumer):
        def cb():
            return 1

        out = consumer.sanitize({"fn": cb})
        assert out["fn"]["$unserializable"] == "function"

    def test_output_is_json_serialisable(self, consumer):
        """The whole point — round-trip through json.dumps must succeed."""
        payload = {
            "queue": asyncio.Queue(),
            "text": "hi",
            "nested": {"cb": lambda: None, "n": 42},
        }
        out = consumer.sanitize(payload)
        json.dumps(out)  # must not raise

    def test_bytes_left_alone_for_offload(self, consumer):
        """`sanitize` doesn't touch bytes — that's `offload_media`'s job."""
        out = consumer.sanitize({"audio": b"\x00\x01\x02"})
        assert out == {"audio": b"\x00\x01\x02"}


# ============================================================
# offload_media — hash-dedup + threshold + inline for small
# ============================================================


class TestOffloadMedia:
    def test_small_bytes_stay_inline(self, consumer, tmp_path):
        small = b"tiny"
        out = consumer.offload_media({"x": small}, tmp_path, threshold=1024)
        assert out == {"x": small}
        # No files written for below-threshold payloads.
        assert not any(tmp_path.iterdir())

    def test_large_bytes_offload(self, consumer, tmp_path):
        big = b"A" * 4096
        out = consumer.offload_media({"audio": big}, tmp_path, threshold=1024)
        ref = out["audio"]
        assert isinstance(ref, dict) and "$media_ref" in ref
        assert ref["size"] == 4096
        # File exists on disk, sha256-named, .bin extension.
        expected = tmp_path / f"{hashlib.sha256(big).hexdigest()}.bin"
        assert expected.exists()
        assert expected.read_bytes() == big

    def test_hash_dedup(self, consumer, tmp_path):
        """Two identical payloads → one file on disk."""
        blob = b"B" * 4096
        consumer.offload_media({"a": blob}, tmp_path, threshold=1024)
        consumer.offload_media({"b": blob}, tmp_path, threshold=1024)
        files = list(tmp_path.iterdir())
        assert len(files) == 1

    def test_numpy_array_offload(self, consumer, tmp_path):
        arr = np.zeros(1024, dtype=np.int16)
        out = consumer.offload_media({"audio": arr}, tmp_path, threshold=512)
        ref = out["audio"]
        assert isinstance(ref, dict)
        assert ref["$media_ref"].endswith(".npy")
        # Round-trip: load the .npy back and compare.
        loaded = np.load(tmp_path / Path(ref["$media_ref"]).name)
        assert loaded.shape == arr.shape

    def test_nested_dict_walk(self, consumer, tmp_path):
        payload = {"level1": {"level2": {"audio": b"X" * 4096}}}
        out = consumer.offload_media(payload, tmp_path, threshold=1024)
        assert "$media_ref" in out["level1"]["level2"]["audio"]

    def test_list_walk(self, consumer, tmp_path):
        payload = {"chunks": [b"Y" * 4096, b"tiny", b"Z" * 4096]}
        out = consumer.offload_media(payload, tmp_path, threshold=1024)
        assert isinstance(out["chunks"][0], dict)  # offloaded
        assert out["chunks"][1] == b"tiny"  # inline
        assert isinstance(out["chunks"][2], dict)  # offloaded


# ============================================================
# truncate — cap + hint
# ============================================================


class TestTruncate:
    def test_under_limit_untouched(self, consumer):
        assert consumer.truncate("hello", limit=100) == "hello"

    def test_over_limit_gets_hint(self, consumer):
        s = "x" * 600
        out = consumer.truncate(s, limit=500)
        assert out.startswith("x" * 500)
        assert out.endswith("…(+100)")
