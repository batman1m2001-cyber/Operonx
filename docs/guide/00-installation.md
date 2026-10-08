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

## Start a project: `operonx init`

```bash
pip install "operonx[serve]"
operonx init myapp                    # or --template http | chat | agent
cd myapp
uv sync && uv run pytest              # the generated tests run offline
uv run operonx serve --list           # the services the app declares
```

`operonx init` writes a project laid out the way the
[guide for coding assistants](https://github.com/batman1m2001-cyber/Operonx/blob/main/operonx/guide/05-project-layout.md)
says:

- `operonx.toml`, which points the CLIs at `app.main:APP`;
- `app/main.py`, where `APP = Application(...)` declares every service and job;
- one feature, `src/<feature>/graph.py` (wiring) and `ops.py` (logic), with its tests;
- `resources.yaml` and `.env.example` (models by key, secrets as `${VAR}`);
- `AGENTS.md`, plus a `CLAUDE.md` holding `@AGENTS.md`, so a coding assistant knows the rules;
- `.operonx/guide/`, a copy of the installed guide.

| Template | What the first feature is |
|---|---|
| `hello` (default) | Pure compute, run as a job and served over HTTP; no model |
| `http` | An HTTP service on a doors graph, tested in-process |
| `chat` | An `LLMOp` on an `llm:assistant` resource; tested against a fake model |
| `agent` | A ReAct agent with one `@tool`; tested with a scripted model |

## See it: operonx-studio

`operonx-studio` is the local web app for a project. It draws every graph
(what runs after what, and which value goes where), plays the graph turn by
turn, and shows runs, evals, jobs and services. It is a tool, not a
dependency of the project, so install it once on its own, from a clone of its repository:

```bash
git clone https://github.com/batman1m2001-cyber/operonx-studio
operonx-studio/install.sh             # again after a `git pull` to upgrade
cd myapp && uv sync
operonx studio                        # http://127.0.0.1:8765; first sign-in root / 123
```

It reads the project with the project's own `.venv` and redraws when you
save. More in the guide page
[Seeing a project](https://github.com/batman1m2001-cyber/Operonx/blob/main/operonx/guide/09-studio.md).

Existing files are never overwritten unless you pass `--force`; on an
existing project `init` only adds what is missing. After upgrading
operonx, `operonx guide --sync` refreshes `.operonx/guide/`.

## The `operonx` command

| Command | Does |
|---|---|
| `operonx init` | create a project (above) |
| `operonx guide` | print the guide for coding assistants; `--path`, `--sync` |
| `operonx serve` | serve the services the application declares; `--list` shows them |
| `operonx run NAME` | run a job or runbook; `--list` shows them |
| `operonx play` | the playground bridge: drive a service's doors over JSON lines |

`operonx-run`, `operonx-serve` and `operonx-play` still
work for this release, as deprecated aliases that print a warning; switch
scripts and Dockerfiles to `operonx <command>`.

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
