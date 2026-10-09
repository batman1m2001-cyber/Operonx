# Installation

You build with operonx through a coding assistant (Claude Code, Codex,
Cursor, …). Start with `operonx init`: it makes a project the assistant can
work in at once.

## Start a project: `operonx init`

With [uv](https://docs.astral.sh/uv/):

```bash
uvx operonx init myapp        # or --template http | chat | agent
cd myapp
uv sync
uv run pytest                 # the generated tests run offline
uv run operonx serve --list   # the services the app declares
```

With pip only (no uv, e.g. behind a company mirror):

```bash
pip install operonx
operonx init myapp
cd myapp
python -m venv .venv
source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -e ".[test]"
pytest
operonx serve --list
```

`init` writes the commands of the toolchain you use into the project's
`AGENTS.md` and `README.md`: uv when it is installed, else pip; `--uv` or
`--pip` chooses. The project's `pyproject.toml` works with both: its test
tools are the `test` extra, which uv's `dev` group includes.

| Template | What the first feature is |
|---|---|
| `hello` (default) | Pure compute, run as a job and served over HTTP; no model |
| `http` | An HTTP service on a doors graph, tested in-process |
| `chat` | An `LLMOp` on an `llm:assistant` resource; tested against a fake model |
| `agent` | An agent with one `@tool` (operonx-agents); tested with a scripted model |

Existing files are never overwritten unless you pass `--force`; on an
existing project `init` only adds what is missing.

## What the project holds

```
myapp/
├── AGENTS.md            the rules your assistant reads first (CLAUDE.md is `@AGENTS.md`)
├── .operonx/guide/      the operonx API guide, for the version installed here
├── app/main.py          the map: every service and job, in one Application
├── src/greeting/        one feature, one folder
│   ├── graph.py         the wiring
│   └── ops.py           the logic
├── tests/               the feature's tests; no test calls a real model
├── datasets/            what jobs run over (.jsonl)
├── resources.yaml       models and stores, by key; secrets as ${VAR}
├── .env.example         the secrets resources.yaml names; copy to .env
├── operonx.toml         points the operonx command at app.main:APP
└── pyproject.toml       works with uv and with pip
```

## What the assistant reads

- **`AGENTS.md`**: the project's rules — the layout, the ladder (op → graph
  → job or service → application), the commands, what never to do. It is
  yours to edit; only the block between the `<!-- operonx:guide -->`
  markers belongs to operonx.
- **`.operonx/guide/`**: how each operonx API is used, copied from the
  installed packages (operonx, and operonx-agents or operonx-kb when
  added). Every example in it is tested against that version. `AGENTS.md`
  tells the assistant to read it before using an API, so it does not write
  operonx from memory.
- **`app/main.py`**: what the product runs.

After an upgrade, refresh the guide and the `AGENTS.md` block. Any
`operonx` command run in the project also notices a stale guide and
refreshes it.

```bash
uv lock --upgrade-package operonx && uv sync && uv run operonx guide   # uv
pip install -U operonx && operonx guide                                # pip, in the .venv
```

## See it: operonx-studio

`operonx-studio` is the local web app for a project. It draws every graph
(what runs after what, and which value goes where), plays the graph turn by
turn, and shows runs, evals, jobs and services. It is a tool, not a
dependency of the project, so install it once on its own, from a clone of
its repository:

```bash
git clone https://github.com/batman1m2001-cyber/operonx-studio
operonx-studio/install.sh     # again after a `git pull`; without bash: pip install ./operonx-studio
cd myapp
uv run operonx studio         # with pip: operonx studio, in the .venv
```

It opens http://127.0.0.1:8765 (first sign-in `root` / `123`), reads the
project with the project's own `.venv`, and redraws when you save. More in
the guide page
[Seeing a project](https://github.com/batman1m2001-cyber/Operonx/blob/main/operonx/guide/09-studio.md).

## The `operonx` command

| Command | Does |
|---|---|
| `operonx init` | create a project (above) |
| `operonx guide` | refresh `.operonx/guide/` and AGENTS.md's block; `--check`, `--path` |
| `operonx run NAME` | run a job; `--list` shows them |
| `operonx serve` | serve the services the application declares; `--list` shows them |
| `operonx play` | the playground bridge: drive a service's doors over JSON lines |
| `operonx eval` | run, compare and report experiments |
| `operonx studio` | open the project in operonx-studio |

## Extras

Provider SDKs are extras. A project names them on the `operonx[...]` line of
its `pyproject.toml` (`init` writes `operonx[serve]`); add one there, e.g.
`operonx[serve,openai]`, then `uv sync` (with pip, `pip install -e ".[test]"`).

| Extra | Contents |
|---|---|
| `openai` | OpenAI and Azure, and any OpenAI-compatible endpoint |
| `anthropic`, `gemini`, `bedrock` | the other model providers |
| `onnx`, `triton`, `huggingface` | local inference (`huggingface` brings torch, ~2.5 GB) |
| `pgvector`, `qdrant`, `faiss`, `postgres`, `mongo`, `clickhouse` | stores |
| `langfuse` | Langfuse tracing |
| `serve` | the HTTP and WebSocket services |
| `mcp` | MCP tools |
| `standard` | OpenAI, Langfuse and serve |
| `all` | every provider, store and tracer (not `huggingface` or `mcp`) |
| `dev`, `docs` | working on operonx itself |

## Python version support

Python 3.10, 3.11, and 3.12 are tested in CI. Older versions are not
supported.

## Configure environment

Provider ops resolve credentials and model configs through a singleton
`ResourceHub`. Two files set this up:

1. **`.env`** — secret values (API keys). Copy `.env.example` to `.env`
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
from operonx import END, START, Operon, graph, op


@op
def double(x: int) -> dict:
    return {"result": x * 2}


@graph
def pure(x):
    d = double(x=x)
    START >> d >> END


result = await Operon(pure, params={"x": None}).run(inputs={"x": 5})
```

## Verify

```bash
python -c "import operonx; print(operonx.__version__)"
```

If you installed an extra, also import a provider symbol:

```bash
python -c "from operonx.providers import LLMOp; print(LLMOp)"
```
