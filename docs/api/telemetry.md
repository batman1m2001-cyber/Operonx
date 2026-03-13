# Telemetry

## Tracer Base Class

::: hush.core.tracing.base.Tracer
    options:
      show_source: true
      members:
        - __init__
        - flush
        - tags
        - stream_trace_limit

## LocalTracer

Zero-dependency JSON file tracer.

::: hush.core.tracing.local.LocalTracer

## TraceNode

::: hush.core.tracing.models.TraceNode

## External Backends

### HushEyesTracer

::: hush.telemetry.tracers.hush_eyes.HushEyesTracer

### LangfuseTracer

::: hush.telemetry.tracers.langfuse.LangfuseTracer

### OTELTracer

::: hush.telemetry.tracers.otel.OTELTracer
