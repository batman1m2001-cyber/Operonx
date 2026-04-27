# Deployment

Operonx ships two HTTP servers — a Python FastAPI server (`operonx[serve]`)
and a Rust Axum server (`operonx-serve` binary). Both expose the same
endpoints over the same JSON contract; pick whichever matches your
operational profile.

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

## Rust: `operonx-serve` binary

```bash
cargo install operonx-serve
operonx-serve --graph ./graph.json --host 0.0.0.0 --port 8000
```

The Rust server is a single static binary — useful for edge deployment
and containers without a Python runtime. Same endpoints, same JSON
contract; graphs portable between the two via the shared schema.

## Configuration

Both servers honour the standard Operonx setup:

- `.env` for credentials.
- `resources.yaml` for model and tracer configs.
- `bootstrap()` (Python) / equivalent Rust call at startup.

For Kubernetes / containerised deployments, mount `resources.yaml` and
provide credentials through the platform's secret store rather than a
file-based `.env`.

## Production checklist

- Set `OPERON_TRACES_DIR` to a persistent volume (or skip the local
  tracer and use Langfuse / OTEL).
- Cap concurrent requests via uvicorn `--limit-concurrency` (Python) or
  the Rust server's `--max-concurrent` flag.
- Wire health checks: `/healthz` returns 200 once the engine is built and
  the resource hub is loaded.
- Pin model versions in `resources.yaml` — never reference `latest`.
- Watch the [Tracing](07-tracing.md) backend for token-cost and latency
  drift.

## Where to go next

- [Architecture overview](../architecture/overview.md) — internals.
- [Rust and Python](../architecture/rust-python.md) — backend choice.
