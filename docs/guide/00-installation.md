# Installation

Operonx is a single Python package with optional extras for each provider
or integration.

## Pick an extra

```bash
pip install operonx                    # Core engine, no providers
pip install "operonx[standard]"        # Recommended — OpenAI + Langfuse + OTEL + serve
pip install "operonx[anthropic]"       # Anthropic SDK
pip install "operonx[gemini]"          # Google Vertex AI
pip install "operonx[bedrock]"         # AWS Bedrock
pip install "operonx[onnx]"            # Local ONNX inference
pip install "operonx[huggingface]"     # transformers + torch (heavy, ~2.5 GB)
pip install "operonx[langfuse]"        # Langfuse tracer
pip install "operonx[otel]"            # OpenTelemetry tracer
pip install "operonx[serve]"           # FastAPI + uvicorn HTTP server
pip install "operonx[all]"             # All providers + tracers (excludes huggingface)
```

| Extra | Contents |
|---|---|
| `standard` | OpenAI + Langfuse + OpenTelemetry + FastAPI/uvicorn |
| `all` | Everything in `standard` plus Anthropic, Gemini, Bedrock, ONNX (excludes `huggingface` for size) |
| `dev` | pytest, ruff, pre-commit |
| `docs` | mkdocs, mkdocs-material, mkdocstrings |

Extras compose: `pip install "operonx[anthropic,langfuse]"`.

The Rust execution backend lives in a separate repo:
[operonx-rs](https://github.com/batman1m2001-cyber/operonx-rs).

## Python version support

Python 3.10, 3.11, and 3.12 are tested in CI. Older versions are not
supported.

## Configure environment

Provider ops resolve credentials and model configs through a singleton
`ResourceHub`. Two files set this up:

1. **`.env`** — secret values (API keys). Copy `env.example` to `.env`
   and fill in the keys you use.
2. **`resources.yaml`** — model and tracer configurations. Reference env
   vars with `${VAR_NAME}`.

Then call `operonx.bootstrap()` once at process startup:

```python
import operonx
operonx.bootstrap()
```

`bootstrap()` is **explicit** — `Operon(graph)` does not auto-load
anything. See [Resource hub](../architecture/resource-hub.md) for the
full setup model and failure surface.

## Pure-compute graphs

If your graph doesn't reference any resource by name (no `LLMOp`,
`EmbeddingOp`, etc.), you can skip `bootstrap()` entirely:

```python
from operonx.core import Operon, GraphOp, op, START, END, PARENT

@op
def double(x: int):
    return {"result": x * 2}

with GraphOp(name="pure") as graph:
    step = double(x=PARENT["x"])
    START >> step >> END

result = await Operon(graph).run(inputs={"x": 5})
```

## Verify

```bash
python -c "import operonx; print(operonx.__version__)"
```

If you installed an extra, also import a provider symbol:

```bash
python -c "from operonx.providers import LLMOp; print(LLMOp)"
```
