# operonx.telemetry

V3 tracing surface — automatic op-level recording, `Consumer` subclasses
convert the recorded `WorkflowTrace` into whatever target format they
want. See `Operon(pipeline, trace=…)` for how to wire consumers.

## Consumer base

::: operonx.telemetry.consumer
    options:
      show_submodules: false

## Concrete consumers

::: operonx.telemetry.consumers.local
    options:
      show_submodules: false

::: operonx.telemetry.consumers.langfuse
    options:
      show_submodules: false

## Langfuse backend

Low-level Langfuse HTTP client + prompt manager — reused by
`LangfuseConsumer` for shipping traces, and available standalone for
prompt fetching.

::: operonx.telemetry.backends
    options:
      show_submodules: false
