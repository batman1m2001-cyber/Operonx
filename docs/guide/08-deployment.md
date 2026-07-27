# Deployment

Operonx ships a Python FastAPI server (`operonx[serve]`).

## Python: `operonx[serve]`

```bash
pip install "operonx[serve]"
```

```python
from operonx.serve import build_app
from operonx.core import Operon

app = build_app(engine_factory=lambda: Operon(my_graph))
```

Run with uvicorn:

```bash
uvicorn myapp:app --host 0.0.0.0 --port 8000
```

Endpoints:

- `POST /run` — synchronous run, returns the final state.
- `POST /stream` — server-sent events stream of frames.
- `GET  /healthz` — readiness probe.

For a static-binary edge deployment, the
[operonx-rs](https://github.com/batman1m2001-cyber/operonx-rs) crate ships
an equivalent Axum server (`operonx-serve` binary) that reads the same
`graph.json` and `resources.yaml`.

## Configuration

The server honours the standard Operonx setup:

- `.env` for credentials.
- `resources.yaml` for model and consumer configs.
- `bootstrap()` at startup.

For Kubernetes / containerised deployments, mount `resources.yaml` and
provide credentials through the platform's secret store rather than a
file-based `.env`.

## Production checklist

- Configure a persistent path for the local trace consumer (or skip it
  and use Langfuse / OTEL).
- Cap concurrent requests via uvicorn `--limit-concurrency`.
- Wire health checks: `/healthz` returns 200 once the engine is built and
  the resource hub is loaded.
- Pin model versions in `resources.yaml` — never reference `latest`.
- Watch the [Tracing](07-tracing.md) backend for token-cost and latency
  drift.

## Where to go next

- [Architecture overview](../architecture/overview.md) — internals.
