# 01 — Hello World (Python)

Three tiny graphs, no API keys. Good first read for the Operonx
authoring DSL — covers `@op`, `@graph`, `>>`, `START`, and `END` in
under 80 lines.

| Scenario   | Ops                       | Shape             |
|------------|---------------------------|-------------------|
| `hello`    | `greet`                   | 1 node            |
| `chain`    | `greet_en → upper`        | 2 nodes in series |
| `parallel` | `step_a + step_b → merge` | fan-out + fan-in  |

## Project layout

```
ex01_hello_world/
├── pyproject.toml    # depends only on operonx (tier 1, no providers)
├── README.md         # this file
└── main.py           # ops + @graph factories + asyncio.run
```

This directory is a self-contained starter project. Copy it anywhere,
run `uv sync`, and you have a working Operonx workspace to build on.
At tier 1 (no providers, no telemetry extras) the install is ~10 MB —
just `pydantic`, `pyyaml`, `rich`, `orjson`.

## Run

```bash
uv sync
uv run python main.py
```

Or with a stdlib venv:

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e .
python main.py
```
