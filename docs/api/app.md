# operonx.app

The application layer: what puts work into an Operon. Services listen
(`[[serve]]`), jobs read a source (`[[job]]`), `operonx.toml` declares
both, and `Application` is that declaration loaded. Graphs never import
this package; the dependency runs one way.

Guides: [Deployment](../guide/08-deployment.md), [Jobs and runbooks](../guide/10-jobs.md),
[The playground bridge](../guide/12-playground.md), [Evals](../guide/13-evals.md).

## Application

::: operonx.app.Application
::: operonx.app.GraphRef

## Declaring it in Python

::: operonx.app.Service
::: operonx.app.websocket
::: operonx.app.http
::: operonx.app.asgi
::: operonx.app.env

## The manifest

::: operonx.app.manifest.Manifest
::: operonx.app.manifest.ServeSpec
::: operonx.app.manifest.JobSpec

## Tracing

`[tracing]` in `operonx.toml`: which trace sinks are on, and the precedence
between it and `trace=` in Python. See [Tracing](../guide/07-tracing.md#switching-sinks-in-operonxtoml).

::: operonx.app.tracing
    options:
      members:
        - Tracing
        - parse_tracing
        - pick
        - settle_serves
        - check_names
        - check_sinks

## Services

::: operonx.app.serve.app.compile_graph
::: operonx.app.serve.ingress
::: operonx.app.serve.egress
::: operonx.app.serve.current_session
::: operonx.app.serve.serve_session
::: operonx.app.serve.protocol.Session
::: operonx.app.serve.protocol.RunRequest
::: operonx.app.serve.protocol.BoundedSession

## Evals

::: operonx.app.evals.Eval
::: operonx.app.evals.Dataset
::: operonx.app.evals.exact
::: operonx.app.evals.contains
::: operonx.app.evals.fuzzy
::: operonx.app.evals.json_match
::: operonx.app.evals.llm_judge
::: operonx.app.evals.Gate
::: operonx.app.evals.compare
::: operonx.app.evals.calibrate
::: operonx.app.evals.load_experiment
::: operonx.app.evals.ExperimentData
::: operonx.app.evals.TraceView
::: operonx.app.evals.OpRow
::: operonx.app.evals.ToolCall
::: operonx.app.evals.trajectory
    options:
      members: [ops, tool_calls, op_output, budget]
::: operonx.app.evals.rescore
::: operonx.app.evals.Rescored
::: operonx.app.evals.publish
::: operonx.app.evals.fingerprint
    options:
      members: [fingerprint, case_hash, dataset_version, evaluator_version]
::: operonx.app.evals.stats
    options:
      members: [wilson, estimate, clustered_se, pass_hat_k, mcnemar, newcombe_paired, paired_bootstrap, compare_paired, holm, benjamini_hochberg]

## The playground bridge

::: operonx.app.play
    options:
      members: false

::: operonx.app.play.Codec
::: operonx.app.play.JsonCodec
::: operonx.app.play.TextCodec
::: operonx.app.play.PcmCodec
::: operonx.app.play.codec_for

## Where a run came from

::: operonx.app.origin
    options:
      members: false

::: operonx.app.origin.origin_metadata
::: operonx.app.origin.in_runbook
::: operonx.app.origin.code_version

Jobs and runbooks are on their own page: [operonx.app.jobs](jobs.md).
Run stores, retention and alerts: [operonx.telemetry.runs](runs.md).
