# Operonx

<p align="center">
  <a href="https://github.com/batman1m2001-cyber/Operonx/actions/workflows/tests.yaml"><img src="https://github.com/batman1m2001-cyber/Operonx/actions/workflows/tests.yaml/badge.svg?branch=main" alt="Tests"></a>
  <a href="https://github.com/batman1m2001-cyber/Operonx/actions/workflows/format.yaml"><img src="https://github.com/batman1m2001-cyber/Operonx/actions/workflows/format.yaml/badge.svg?branch=main" alt="Format"></a>
  <a href="https://batman1m2001-cyber.github.io/Operonx/"><img src="https://github.com/batman1m2001-cyber/Operonx/actions/workflows/docs.yaml/badge.svg?branch=main" alt="Docs"></a>
  <a href="https://codecov.io/gh/batman1m2001-cyber/Operonx"><img src="https://codecov.io/gh/batman1m2001-cyber/Operonx/branch/main/graph/badge.svg" alt="Coverage"></a>
  <a href="https://pypi.org/project/operonx/"><img src="https://img.shields.io/pypi/v/operonx?label=PyPI" alt="PyPI"></a>
  <img src="https://img.shields.io/badge/python-3.10%2B-blue" alt="Python">
  <a href="https://github.com/batman1m2001-cyber/Operonx/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-Apache%202.0-green" alt="License"></a>
</p>

**Operonx** is a Python workflow engine whose ops can `yield`. The same
graph runs as a batch job over a file, as a streaming pipeline (a voice bot:
audio → STT → LLM → TTS), or behind an HTTP or WebSocket service.

You build with it through a coding assistant (Claude Code, Codex, Cursor,
…). `operonx init` makes a project the assistant can work in at once: a
layout, a first feature with its tests, the project's rules in `AGENTS.md`,
and the API guide of your installed operonx beside the code.

## Start a project

With [uv](https://docs.astral.sh/uv/):

```bash
uvx operonx init myapp     # uvx runs operonx without installing it
cd myapp
uv sync
uv run pytest              # the first feature's tests, offline
```

With pip only (no uv, e.g. behind a company mirror):

```bash
pip install operonx
operonx init myapp
cd myapp
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -e ".[test]"
pytest
```

`operonx init` writes the commands of the toolchain you use into the
project's docs: uv when it is installed, else pip (`--uv` or `--pip` to
choose). The first feature is `--template hello` (pure compute, a job and an
HTTP service), `http`, `chat` (one LLM call) or `agent` (an agent with a
tool).

Then open the folder in your coding assistant and ask for what you want:
*"add a feature that scores each call in `datasets/calls.jsonl` with the
assistant model, and a job that runs it nightly"*.

## What you get

```
myapp/
├── AGENTS.md            the rules your assistant reads first (CLAUDE.md is `@AGENTS.md`)
├── .operonx/guide/      the operonx API guide, for the version installed here
├── app/main.py          the map: every service and job, in one Application
├── src/greeting/        one feature, one folder
│   ├── graph.py         the wiring: which op runs after which, what feeds what
│   └── ops.py           the logic: plain Python functions
├── tests/               the feature's tests; no test calls a real model
├── datasets/            what jobs run over (.jsonl)
├── resources.yaml       models and stores, by key (`llm:assistant`)
├── .env.example         the secrets resources.yaml names; copy to .env
├── operonx.toml         points the operonx command at app/main.py
└── pyproject.toml       works with uv and with pip
```

## What your assistant reads

| File | What it is | Who writes it |
|---|---|---|
| `AGENTS.md` | the project's rules: the layout, the ladder (op → graph → job or service → application), the commands, what never to do | you; start from what `init` wrote |
| `.operonx/guide/` | how every operonx API is used; every example in it is tested against the installed version | operonx, never edit |
| `app/main.py` | what the product runs | you and the assistant |

`AGENTS.md` tells the assistant to read the guide before it uses an operonx
API, so it does not write operonx from memory or from an older version.
The block between `<!-- operonx:guide -->` markers in `AGENTS.md` belongs to
operonx; the rest is yours.

After an upgrade, `operonx guide` refreshes the guide and that block. Any
`operonx` command run in the project notices a stale guide and refreshes it.

```bash
uv lock --upgrade-package operonx && uv sync && uv run operonx guide   # uv
pip install -U operonx && operonx guide                                # pip
```

## Run it, serve it, see it

```bash
uv run operonx run greet_people   # a job: the graph once per line of datasets/people.jsonl
uv run operonx serve              # the services: POST /greet on :8000
uv run operonx studio             # the project in operonx-studio, in the browser
```

With pip, the same commands without `uv run`, in the activated `.venv`.

[operonx-studio](https://github.com/batman1m2001-cyber/operonx-studio)
draws every graph (what runs after what, and which value goes where), lets
you talk to a service turn by turn, and shows runs, evals, jobs and
services. It is a separate tool, installed once from its repository:

```bash
git clone https://github.com/batman1m2001-cyber/operonx-studio
operonx-studio/install.sh            # or, without bash: pip install ./operonx-studio
```

## What the code looks like

An op is a Python function. One that `yield`s runs what follows it once per
item. A `@graph` wires ops with `>>`:

```python
from operonx import END, START, graph, op


@op
def words(text: str):
    for w in text.split():
        yield {"word": w}


@op
def shout(word: str) -> dict:
    return {"loud": word.upper()}


@graph
def flow(text):
    w = words(text=text)
    s = shout(word=w["word"])
    START >> w >> s >> END
```

`app/main.py` decides how a graph runs: a `Job` over a file, or a `Service`
behind `http(...)`, `websocket(...)`, `webhook(...)` or `schedule(...)`.

## Extras

Provider SDKs are extras. A project names its extras on the `operonx[...]`
line of `pyproject.toml` (`init` writes `operonx[serve]`); add one there,
e.g. `operonx[serve,openai]`, then `uv sync` (with pip, `pip install -e ".[test]"`).

| Extra | Contents |
|---|---|
| `openai` | OpenAI and Azure, and any OpenAI-compatible endpoint (vLLM, TEI) |
| `anthropic`, `gemini`, `bedrock` | the other model providers |
| `onnx`, `triton`, `huggingface` | local inference (`huggingface` brings torch, ~2.5 GB) |
| `pgvector`, `qdrant`, `faiss`, `postgres`, `mongo`, `clickhouse` | stores |
| `langfuse` | Langfuse tracing |
| `serve` | the HTTP and WebSocket services |
| `mcp` | MCP tools |
| `standard` | OpenAI, Langfuse and serve |
| `all` | every provider, store and tracer (not `huggingface` or `mcp`) |

[operonx-agents](https://pypi.org/project/operonx-agents/) (agents) and
[operonx-kb](https://pypi.org/project/operonx-kb/) (knowledge bases) are
separate packages; each adds its own pages to `.operonx/guide/`.

## Documentation

| For | Where |
|---|---|
| your coding assistant | [`operonx/guide/`](operonx/guide/README.md), copied into every project as `.operonx/guide/` |
| reading it yourself | [the documentation site](https://batman1m2001-cyber.github.io/Operonx/) |
| runnable examples | [examples/python/](examples/python/) |
| what changed | [CHANGELOG.md](CHANGELOG.md) |

## Contributing

```bash
git clone https://github.com/batman1m2001-cyber/Operonx.git
cd Operonx
uv sync --all-extras
pre-commit install
uv run pytest tests/ -m "not integration"
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for the full contributor guide.

## License

Apache 2.0
