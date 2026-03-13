"""Tests for HushEyesTracer."""

import time

import pytest
from hush.core import END, PARENT, START, GraphOp, Hush
from hush.core.ops import FuncOp

from hush.telemetry import HushEyesTracer


class TestHushEyesTracerBasic:
    def test_creation(self):
        tracer = HushEyesTracer()
        assert tracer._url == "http://127.0.0.1:8420/api/ingest"
        assert tracer.tags == []

    def test_creation_with_tags(self):
        tracer = HushEyesTracer(tags=["prod", "v2"])
        assert tracer.tags == ["prod", "v2"]

    def test_custom_host_port(self):
        tracer = HushEyesTracer(host="10.0.0.1", port=9999)
        assert tracer._url == "http://10.0.0.1:9999/api/ingest"

    def test_repr(self):
        tracer = HushEyesTracer()
        assert "HushEyesTracer" in repr(tracer)
        assert "8420" in repr(tracer)

    def test_flush_no_server_does_not_raise(self):
        """flush() should not raise even when server is not running."""
        tracer = HushEyesTracer(port=19999)  # unlikely to have server here
        tracer.flush({"request_id": "test", "records": []})


# ---------------------------------------------------------------------------
# Integration: HushEyesTracer (requires running ui-hush-eyes server)
# ---------------------------------------------------------------------------
@pytest.mark.integration
class TestHushEyesIntegration:
    @pytest.mark.asyncio
    async def test_hush_eyes_tracer_end_to_end(self):
        """End-to-end test: workflow -> HushEyesTracer -> ui-hush-eyes server."""
        with GraphOp(name="eyes-test") as graph:
            node = FuncOp(
                name="double",
                code_fn=lambda x: {"result": x * 2},
                inputs={"x": PARENT["x"]},
                outputs={"result": PARENT},
            )
            START >> node >> END

        tracer = HushEyesTracer(tags=["integration-test"])
        engine = Hush(graph, tracer=tracer)

        result = await engine.run(
            inputs={"x": 21},
            request_id="eyes-integration-001",
        )

        assert result["result"] == 42

        # Give background thread time to POST
        time.sleep(1.0)
        # Manual verification: open http://localhost:8420 and check trace
