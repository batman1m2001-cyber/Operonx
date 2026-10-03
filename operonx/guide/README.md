# operonx — a guide for coding assistants

Read this before writing code that uses operonx. Every example in these
pages is a complete program, and the operonx test suite runs each one
against the version this guide ships with (`operonx --version`).

operonx is an async graph engine. You write **ops** (Python functions), wire
them into a **graph**, and run it with **`Operon`**. The same graph runs as
a **Job** over a batch of items or as a **Service** behind HTTP or a
websocket, and an **Application** bundles a product's jobs and services.

## Read in this order

1. [Op types](01-ops.md): `@op`, generators, `@graph`, `LLMOp`, agents,
   flow and retrieval ops.
2. [The composition ladder](02-composition.md): op → operon → Job /
   Runbook / Service → Application → `operonx.toml` and the CLIs.
3. [Control flow](03-control-flow.md): streaming, loops, if/else, `~`.
4. [Gotchas](04-gotchas.md): the failures that raise nothing.
5. [Project layout](05-project-layout.md): how to lay out a product.
6. [Failures](06-failures.md): `retry=`, `timeout=`, `on_error`,
   `errors="raise"`, `max_concurrency=`, concurrent writers.
7. [Evals](07-evals.md): cases, repeats, a gate with a baseline, exit codes;
   `operonx eval`, reports, calibrate, the pytest plugin.

## Install

```bash
pip install "operonx[serve,openai]"   # serve: HTTP/websocket; openai: OpenAI-compatible models
```

Other extras: `anthropic`, `gemini`, `langfuse`, `postgres`, `mongo`,
`clickhouse`, `mcp`, `standard` (common set), `all`.

## Start a project: `operonx init`

```bash run
operonx init myapp          # the hello template; also --template http | chat | agent
```

It writes the layout of [project layout](05-project-layout.md): `operonx.toml`,
`resources.yaml`, `.env.example`, `app/main.py` with the `Application`, one
feature in `src/<feature>/`, its tests, and `AGENTS.md` (with `CLAUDE.md`
pointing at it) for coding assistants. It also copies this guide into
`myapp/.operonx/guide/`. Existing files are kept unless `--force`.

## Imports

```python
from operonx import END, PARENT, SCRATCH, START, EmitOp, InterruptOp, Operon, bootstrap, graph, op
from operonx import OpFailed, Retry, Timeout  # failure policies (page 6)
from operonx.agents import agent_result, build_react_agent, get_tool_definitions, tool
from operonx.agents.ops.model_ops import make_llm_caller
from operonx.app import Application, Eval, Service, asgi, env, http, schedule, webhook, websocket
from operonx.app.jobs import Job, Runbook
from operonx.app.serve import RunRequest, egress, ingress
from operonx.core.ops import if_
from operonx.providers.ops import (
    EmbeddingOp,
    LLMOp,
    RerankOp,
    VectorDeleteOp,
    VectorSearchOp,
    VectorUpsertOp,
)
```

## The rules that matter most

1. **Write ops in `.py` files and return a dict literal.** Its keys are the
   op's outputs.
2. **`a >> b` orders; `b(x=a["y"])` only reads.** Always draw the edge; a
   read nothing orders fails the build.
3. **A graph parameter is a runtime input only with
   `Operon(g, params={"x": None})`** — and inside the body it already is
   `PARENT["x"]`: use it by name, never `PARENT["x"]` beside it.
4. **An op that raises does not raise.** Its outputs are just missing;
   the run's result has `"$errors"` naming it. `retry=`, `timeout=`,
   `op.on_error(handler)` and `errors="raise"` change that (page 6).
5. **Streaming is sequential per item by default.** `.parallel()` to fan
   out, `.collect()` to gather.
6. **Loop state lives in `PARENT.declare(...)` cells**, and loops exit to
   `END`.
7. **Branch arms merge by themselves.** Give merge inputs defaults.
8. **Combine conditions with `&`, `|`, `~`**, never `and`, `or`, `not`.
9. **`~` is only for races:** fire on whichever of two unrelated ops lands
   first.
10. **Models and stores are `resources.yaml` keys**; call
    `operonx.bootstrap()` (or `Application.bootstrap()`) before building an
    engine that uses them.

## Where this guide lives

It ships inside the package, so it always matches the installed version:

```bash
operonx guide --path           # where the installed guide is
operonx guide --sync           # copy it into the project's .operonx/guide/ (after an upgrade)
python -m operonx.guide        # this page's path and the table of contents
```
