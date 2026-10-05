# DX §1.3 — developer experience

Status: plan (2026-10-05). Roadmap `docs/roadmap/ROADMAP.md` §1.3, findings C16, C17.

## 1. Audit

| Item | Today |
|---|---|
| C16 fake LLM | none — every template and test hand-rolls an OpenAI-compatible HTTP server |
| C16 `init` in a uv project | `operonx init` pins PyPI operonx even inside a checkout of operonx |
| C17 reserved keywords | 21 op settings (`id`, `name`, `start`, `stream`, …) share the call's keyword space with the function's own parameters. `@op def f(id)`, called `f(id=7)`: the 7 is taken as the op's id and **never reaches the function** — a warning at decoration is all there is |
| Logging | level only from `LOG_LEVEL` (a name other tools use too — a repo `.env` setting it leaked into every test, R3); a fixed slow-op threshold that also fires on LLM/IO ops whose slowness is the provider's |
| `operonx serve` | no `--host/--port/--reload`; no health route |
| Final cell values | a result shows what the run *wrote* to a reducer cell, not the cell; the final value needs `out["$state"][root, var]` and the root's name |

## 2. Decisions

| # | Decision |
|---|---|
| X1 | `api_type: fake` LLM: `script:` a list of turns — a string (text), `{tool_calls: [...]}`, `{status: 429}` (raises the provider error a real one would), `{delay: 0.2, text: …}`; `stream` yields the text in chunks. Turns are taken in order, the last repeats. No network. Templates' tests use it |
| X2 | `operonx init --editable PATH` pins `operonx = {path = PATH, editable = true}`; inside a uv project whose root is an operonx checkout, that is the default |
| X3 | C17: **a keyword the function itself takes is its input** — the rule `timeout=` already follows. A setting whose name the function uses is given with `f.configure(id="x", retry=Retry(3))(id=7)`; `configure` refuses a name that is not a setting. The decoration-time warning goes. Flat settings keep working; their deprecation (D4) waits for the release that announces 2.0, since today every flat `name=` in callbot would warn |
| X4 | `OPERONX_LOG_LEVEL` wins over `LOG_LEVEL`; handlers write to stderr; `OPERONX_SLOW_OP_MS` (default today's) sets the threshold, and ops of type `llm`, `embedding`, `rerank` and door ops are exempt |
| X5 | `operonx serve --host --port --reload`; `--host` defaults to `127.0.0.1` unless `OPERONX_ENV=production`; every listener answers `GET /healthz` `{"ok": true, "services": [...]}` unless a service already owns that path |
| X6 | `out["$cells"]`: the root's declared cells' final values, by name |
