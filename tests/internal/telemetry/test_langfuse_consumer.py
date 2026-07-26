"""Unit tests for `LangfuseConsumer` — batch shape + parent strategies.

Uses a `FakeLangfuseClient` — no network, no creds needed. Asserts on
what the consumer POSTs (the `ingest()` batch) rather than what
Langfuse would render.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List

import pytest

from operonx.core.workflow_trace import (
    STATUS_ERROR,
    STATUS_OK,
    OpExecution,
    UpstreamRef,
    WorkflowTrace,
)
from operonx.telemetry.consumers.langfuse import LangfuseConsumer


# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------


@dataclass
class FakeLangfuseClient:
    """Records `ingest()` calls; returns a canned trace URL."""

    calls: List[List[Dict[str, Any]]] = field(default_factory=list)

    def ingest(self, batch, timeout: int = 30) -> Dict[str, Any]:
        self.calls.append(list(batch))
        return {"successes": [], "errors": []}

    def trace_url(self, trace_id: str) -> str:
        return f"https://langfuse.test/trace/{trace_id}"


def _mkexec(op_id, op_name, start, end, ctx=("main",),
            inputs=None, outputs=None, upstreams=None, status=STATUS_OK,
            error=None):
    return OpExecution(
        op_id=op_id, op_name=op_name, op_full_name=f"engine.{op_name}",
        ctx=ctx, start_time=start, end_time=end,
        inputs=inputs or {}, outputs=outputs or {},
        upstreams=upstreams or [], status=status, error=error,
    )


def _u(from_op_id, from_name="src", from_key="out", to_key="in"):
    return UpstreamRef(
        from_op_id=from_op_id,
        from_op_name=from_name,
        from_op_full_name=f"engine.{from_name}",
        from_key=from_key,
        to_key=to_key,
    )


@pytest.fixture
def linear_trace():
    """A → B → C (each op depends on the previous)."""
    return WorkflowTrace(
        trace_id="t-lin", workflow_name="w",
        started_at=100.0, ended_at=100.3,
        nodes=[
            _mkexec("A", "a", 100.00, 100.10),
            _mkexec("B", "b", 100.10, 100.20, upstreams=[_u("A", "a")]),
            _mkexec("C", "c", 100.20, 100.30, upstreams=[_u("B", "b")]),
        ],
        metadata={"user_id": "u-1", "session_id": "s-1"},
    )


@pytest.fixture
def client():
    return FakeLangfuseClient()


# ---------------------------------------------------------------------------
# Batch shape — one trace-create + one span-create per node
# ---------------------------------------------------------------------------


class TestBatchShape:
    def test_batch_has_one_trace_and_n_spans(self, linear_trace, client):
        LangfuseConsumer(config={"client": client}).consume(linear_trace)
        assert len(client.calls) == 1
        batch = client.calls[0]
        types = [e["type"] for e in batch]
        assert types == ["trace-create", "span-create", "span-create", "span-create"]

    def test_trace_create_carries_metadata_and_name(self, linear_trace, client):
        LangfuseConsumer(config={"client": client, "workflow_name": "callbot"}).consume(linear_trace)
        trace_ev = client.calls[0][0]
        body = trace_ev["body"]
        assert body["id"] == "t-lin"
        assert body["name"] == "callbot"
        assert body["userId"] == "u-1"
        assert body["sessionId"] == "s-1"

    def test_span_body_has_input_output_ctx(self, client):
        node = _mkexec("A", "classify", 1.0, 1.2,
                       ctx=("main", "[1]"),
                       inputs={"state": "MAIN"}, outputs={"intent": "affirm"})
        trace = WorkflowTrace("t", "w", 0.0, 1.0, nodes=[node])
        LangfuseConsumer(config={"client": client}).consume(trace)
        span_body = client.calls[0][1]["body"]
        assert span_body["name"] == "classify"
        assert span_body["input"] == {"state": "MAIN"}
        assert span_body["output"] == {"intent": "affirm"}
        assert span_body["metadata"]["ctx"] == "main.[1]"


# ---------------------------------------------------------------------------
# Parent-picking strategies
# ---------------------------------------------------------------------------


class TestParentStrategies:
    def test_first_upstream_default(self, linear_trace, client):
        LangfuseConsumer(config={"client": client}).consume(linear_trace)
        spans = [e for e in client.calls[0] if e["type"] == "span-create"]
        parents = {s["body"]["id"]: s["body"]["parentObservationId"] for s in spans}
        assert parents == {"A": None, "B": "A", "C": "B"}

    def test_root_only(self, linear_trace, client):
        LangfuseConsumer(
            config={"client": client, "parent_strategy": "root_only"}
        ).consume(linear_trace)
        spans = [e for e in client.calls[0] if e["type"] == "span-create"]
        assert all(s["body"]["parentObservationId"] is None for s in spans)

    def test_sequential(self, client):
        # Nodes deliberately in ctx-fan-out order but distinct start_times.
        trace = WorkflowTrace(
            "t", "w", 0.0, 1.0,
            nodes=[
                _mkexec("A", "a", 0.10, 0.11),
                _mkexec("B", "b", 0.20, 0.21),  # No upstream, but should
                                                # attach to A via sequential
                _mkexec("C", "c", 0.30, 0.31),
            ],
        )
        LangfuseConsumer(
            config={"client": client, "parent_strategy": "sequential"}
        ).consume(trace)
        spans = [e for e in client.calls[0] if e["type"] == "span-create"]
        parents = {s["body"]["id"]: s["body"]["parentObservationId"] for s in spans}
        assert parents == {"A": None, "B": "A", "C": "B"}


# ---------------------------------------------------------------------------
# Error status + missing client
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_missing_client_raises(self):
        with pytest.raises(ValueError, match="requires a `client`"):
            LangfuseConsumer(config={}).consume(
                WorkflowTrace("t", "w", 0.0, 0.0)
            )

    def test_errored_op_gets_error_level(self, client):
        node = _mkexec("X", "boom", 0.0, 0.1,
                       status=STATUS_ERROR, error="RuntimeError: kaput")
        trace = WorkflowTrace("t", "w", 0.0, 0.1, nodes=[node])
        LangfuseConsumer(config={"client": client}).consume(trace)
        span_body = client.calls[0][1]["body"]
        assert span_body["level"] == "ERROR"
        assert "kaput" in span_body["statusMessage"]

    def test_returns_trace_url(self, linear_trace, client):
        result = LangfuseConsumer(config={"client": client}).consume(linear_trace)
        assert result == "https://langfuse.test/trace/t-lin"
