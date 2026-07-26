"""Tests for `trace=` param on `Operon.__init__` — the V3 wiring.

Covers:
* `trace=None` → no consumers, no invocation.
* `trace="key"` → resolves via ResourceHub.
* `trace=<Consumer instance>` → used directly, no hub round-trip.
* `trace=[…mixed…]` → list of both forms.
* Failing consumer → logged, does NOT fail the run.
* Bad type → TypeError at construction.
"""

from __future__ import annotations

import tempfile
import textwrap
from pathlib import Path
from typing import Any, List

import pytest

from operonx.core import END, PARENT, START, Operon, graph
from operonx.core.ops import op
from operonx.core.registry import ResourceHub
from operonx.core.workflow_trace import WorkflowTrace
from operonx.telemetry.consumer import Consumer

# ---------------------------------------------------------------------------
# Fixture ops + graph
# ---------------------------------------------------------------------------


@op
def _add(a: int, b: int):
    return {"sum": a + b}


def _mk_engine(**operon_kwargs):
    @graph
    def wf(a: int, b: int):
        s = _add(a=PARENT["a"], b=PARENT["b"])
        START >> s >> END

    return Operon(wf, params={"a": 0, "b": 0}, **operon_kwargs)


# ---------------------------------------------------------------------------
# Recorder + failing consumer for assertions
# ---------------------------------------------------------------------------


class RecordingConsumer(Consumer):
    """Captures every consume() call for later assertion."""

    def __init__(self):
        super().__init__()
        self.calls: List[WorkflowTrace] = []

    def consume(self, trace: WorkflowTrace):
        self.calls.append(trace)
        return f"recorded:{trace.trace_id}"


class BoomConsumer(Consumer):
    def consume(self, trace: WorkflowTrace):
        raise RuntimeError("intentional")


# ---------------------------------------------------------------------------
# Cases
# ---------------------------------------------------------------------------


class TestTraceParamNone:
    async def test_none_means_no_consumers(self):
        engine = _mk_engine(trace=None)
        assert engine._trace_consumers == []
        # Run should complete cleanly without any consumer machinery.
        handle = engine.start(inputs={"a": 1, "b": 2})
        result = await handle.collect(unwrap=True)
        assert result["sum"] == 3


class TestTraceParamInstance:
    async def test_direct_consumer_instance(self):
        rec = RecordingConsumer()
        engine = _mk_engine(trace=rec)
        assert engine._trace_consumers == [rec]

        handle = engine.start(inputs={"a": 3, "b": 4})
        await handle.collect()
        # Consumer invoked exactly once with this run's trace.
        assert len(rec.calls) == 1
        assert rec.calls[0] is handle.trace

    async def test_list_of_instances(self):
        a, b = RecordingConsumer(), RecordingConsumer()
        engine = _mk_engine(trace=[a, b])
        handle = engine.start(inputs={"a": 1, "b": 1})
        await handle.collect()
        assert len(a.calls) == 1
        assert len(b.calls) == 1


class TestTraceParamResourceKey:
    async def test_string_resolves_via_resource_hub(self, tmp_path):
        yaml = textwrap.dedent(f"""
            trace_local:
              default:
                root: {tmp_path}
                show_io: false
        """)
        cfg = tmp_path / "resources.yaml"
        cfg.write_text(yaml)
        ResourceHub.set_instance(ResourceHub.from_yaml(str(cfg)))
        # Import consumers to trigger registration.
        import operonx.telemetry  # noqa: F401

        engine = _mk_engine(trace="trace_local:default")
        # Resource resolved eagerly at __init__.
        assert len(engine._trace_consumers) == 1
        from operonx.telemetry.consumers.local import LocalConsumer

        assert isinstance(engine._trace_consumers[0], LocalConsumer)

        handle = engine.start(inputs={"a": 5, "b": 6}, request_id="t-str")
        await handle.collect()
        # LocalConsumer wrote a directory for this run.
        assert (tmp_path / "t-str").is_dir()
        assert (tmp_path / "t-str" / "nodes.jsonl").exists()


class TestTraceParamMixed:
    async def test_string_and_instance_together(self, tmp_path):
        yaml = textwrap.dedent(f"""
            trace_local:
              default:
                root: {tmp_path}
                show_io: false
        """)
        cfg = tmp_path / "resources.yaml"
        cfg.write_text(yaml)
        ResourceHub.set_instance(ResourceHub.from_yaml(str(cfg)))
        import operonx.telemetry  # noqa: F401

        rec = RecordingConsumer()
        engine = _mk_engine(trace=["trace_local:default", rec])
        handle = engine.start(inputs={"a": 7, "b": 8}, request_id="t-mix")
        await handle.collect()
        assert len(rec.calls) == 1
        assert (tmp_path / "t-mix" / "nodes.jsonl").exists()


class TestTraceParamFailure:
    async def test_failing_consumer_does_not_fail_the_run(self, caplog):
        rec = RecordingConsumer()
        engine = _mk_engine(trace=[BoomConsumer(), rec])
        handle = engine.start(inputs={"a": 1, "b": 1})
        # Run completes even though one consumer raised.
        result = await handle.collect(unwrap=True)
        assert result["sum"] == 2
        # The non-failing one still ran.
        assert len(rec.calls) == 1


class TestTraceParamBadType:
    def test_type_error_at_construction(self):
        with pytest.raises(TypeError, match="Consumer instance"):
            _mk_engine(trace=42)
