"""Tests for the legacy → pipeline adapter.

Covers ``trace_filter_to_processors`` mapping + ``LangfuseTracer.as_pipeline``
migration helper. Mocked client; integration round-trip is the probe.
"""

from operonx.core.tracing.legacy import trace_filter_to_processors
from operonx.core.tracing.pipeline import TracePipeline
from operonx.core.tracing.processors.drop import DropEmpty, DropOps, KeepOps
from operonx.core.tracing.processors.truncate import TruncateIO
from operonx.core.tracing.trace_filter import TraceFilter

# =============================================================================
# trace_filter_to_processors
# =============================================================================


class TestTraceFilterToProcessors:
    def test_empty_filter_yields_no_processors(self):
        assert trace_filter_to_processors(TraceFilter()) == []

    def test_skip_empty_maps_to_drop_empty(self):
        out = trace_filter_to_processors(TraceFilter(skip_empty=True))
        assert len(out) == 1
        assert isinstance(out[0], DropEmpty)

    def test_exclude_ops_maps_to_drop_ops(self):
        out = trace_filter_to_processors(TraceFilter(exclude_ops=["picker", "skip_stt"]))
        assert len(out) == 1
        assert isinstance(out[0], DropOps)
        # Internal name set is private; verify via behavior
        assert out[0]._names == frozenset({"picker", "skip_stt"})

    def test_include_ops_maps_to_keep_ops(self):
        out = trace_filter_to_processors(TraceFilter(include_ops=["a", "b"]))
        assert len(out) == 1
        assert isinstance(out[0], KeepOps)

    def test_max_io_size_maps_to_truncate(self):
        out = trace_filter_to_processors(TraceFilter(max_io_size=2000))
        assert len(out) == 1
        assert isinstance(out[0], TruncateIO)
        assert out[0].max_bytes == 2000

    def test_max_io_size_zero_no_truncate(self):
        # Legacy convention: 0 = no limit
        out = trace_filter_to_processors(TraceFilter(max_io_size=0))
        assert all(not isinstance(p, TruncateIO) for p in out)

    def test_exclude_kinds_logged_not_mapped(self, caplog):
        # The legacy "kind" axis (batch/generator/stream_context) doesn't
        # apply in the event model — we log INFO and drop.
        with caplog.at_level("INFO", logger="operonx.tracing"):
            out = trace_filter_to_processors(
                TraceFilter(exclude_kinds=["stream_context", "loop_iter"])
            )
        assert out == []
        assert any("ignoring exclude_kinds" in rec.message for rec in caplog.records)

    def test_preserve_children_of_dropped_silently(self):
        # No-longer-needed field; drops cleanly without warning.
        out = trace_filter_to_processors(TraceFilter(preserve_children_of=["audio", "vad"]))
        assert out == []

    def test_protected_types_default_dropped_silently(self):
        # Default ["trace", "generation"] is just dropped (no debug log).
        out = trace_filter_to_processors(TraceFilter())
        assert out == []

    def test_rewriters_dropped_silently(self):
        # Rewriters expect a tree shape not present in the event stream;
        # they are silently dropped rather than wrapped in a fake no-op.
        def my_rewriter(nodes):
            return nodes

        out = trace_filter_to_processors(TraceFilter(rewriters=[my_rewriter]))
        assert out == []

    def test_full_filter_compose_in_order(self):
        """Order: drop_ops → keep_ops → drop_empty → truncate."""
        tf = TraceFilter(
            skip_empty=True,
            exclude_ops=["picker"],
            max_io_size=2000,
        )
        out = trace_filter_to_processors(tf)
        # Order: exclude_ops first (per the legacy field order), then
        # skip_empty, then truncate. Verify by type.
        types = [type(p).__name__ for p in out]
        assert types == ["DropOps", "DropEmpty", "TruncateIO"]


# =============================================================================
# LangfuseTracer.as_pipeline
# =============================================================================


class TestLangfuseTracerAsPipeline:
    def test_returns_trace_pipeline(self):
        from operonx.telemetry import LangfuseTracer

        # Use a fake config to avoid hitting ResourceHub
        from operonx.telemetry.backends.langfuse import LangfuseConfig

        config = LangfuseConfig(
            public_key="pk-x",
            secret_key="sk-x",
            host="https://mock.local",
        )
        pipeline = LangfuseTracer.as_pipeline(config=config, tags=["test"])
        assert isinstance(pipeline, TracePipeline)
        assert len(pipeline.exporters) == 1
        # default = tree exporter
        from operonx.telemetry.exporters import LangfuseTreeExporter

        assert isinstance(pipeline.exporters[0], LangfuseTreeExporter)

    def test_grouped_timeline_returns_grouped_exporter(self):
        from operonx.telemetry import LangfuseTracer
        from operonx.telemetry.backends.langfuse import LangfuseConfig
        from operonx.telemetry.exporters import LangfuseGroupedTimelineExporter

        config = LangfuseConfig(
            public_key="pk-x",
            secret_key="sk-x",
            host="https://mock.local",
        )
        pipeline = LangfuseTracer.as_pipeline(
            config=config,
            grouped_timeline=True,
        )
        assert isinstance(pipeline.exporters[0], LangfuseGroupedTimelineExporter)

    def test_trace_filter_converts_to_processors(self):
        from operonx.telemetry import LangfuseTracer
        from operonx.telemetry.backends.langfuse import LangfuseConfig

        config = LangfuseConfig(
            public_key="pk-x",
            secret_key="sk-x",
            host="https://mock.local",
        )
        tf = TraceFilter(
            skip_empty=True,
            exclude_ops=["picker"],
            max_io_size=1500,
        )
        pipeline = LangfuseTracer.as_pipeline(config=config, trace_filter=tf)
        types = [type(p).__name__ for p in pipeline.processors]
        assert "DropOps" in types
        assert "DropEmpty" in types
        assert "TruncateIO" in types

    def test_no_trace_filter_means_no_processors(self):
        from operonx.telemetry import LangfuseTracer
        from operonx.telemetry.backends.langfuse import LangfuseConfig

        config = LangfuseConfig(
            public_key="pk-x",
            secret_key="sk-x",
            host="https://mock.local",
        )
        pipeline = LangfuseTracer.as_pipeline(config=config)
        assert pipeline.processors == []
