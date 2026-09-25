# operonx.app

The application layer: what puts work into an Operon. Services listen
(`[[serve]]`), jobs read a source (`[[job]]`), `operonx.toml` declares
both, and `Application` is that declaration loaded. Graphs never import
this package; the dependency runs one way.

Guides: [Deployment](../guide/08-deployment.md), [Jobs and runbooks](../guide/10-jobs.md).

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

## Services

::: operonx.app.serve.app.compile_graph
::: operonx.app.serve.ingress
::: operonx.app.serve.egress
::: operonx.app.serve.current_session
::: operonx.app.serve.serve_session
::: operonx.app.serve.protocol.Session
::: operonx.app.serve.protocol.RunRequest
::: operonx.app.serve.protocol.BoundedSession

Jobs and runbooks are on their own page: [operonx.app.jobs](jobs.md).
