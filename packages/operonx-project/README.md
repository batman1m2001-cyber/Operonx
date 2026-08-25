# operonx-project

Project conventions, manifest, and graph extraction for operonx.

This package is what a *project* depends on. It is deliberately
dependency-minimal — it runs inside the target project's own environment in
order to import that project's code, so it must impose nothing of its own.
The web UI lives in the separate `operonx-studio` distribution.

## What it provides

- **`operonx.toml`** — the project manifest. Declares which graphs are entry
  points, what must be injected to build them, and where resources come
  from. See `Manifest.load()`.

## Manifest

```toml
[project]
name = "callbot"

[resources]
base    = "~/.operonx/common.yaml"   # optional shared hub
overlay = "resources.yaml"           # merged over base

[[graph]]
name  = "ws_callbot_pipeline"
entry = "callbot.graph:build_ws_callbot_pipeline"
[graph.bind]
agent = "agents.ahamove_hr.agent:AhamoveHRAgent"
```

`entry` points at either a `@graph` — every parameter is then a runtime
input port — or a builder function, whose parameters are build-time
injections and must appear in `bind`.

## Design

See [`docs/design/UI_PLATFORM_PLAN.md`](../../docs/design/UI_PLATFORM_PLAN.md)
for the conventions (C1–C7) these APIs enforce and why.
