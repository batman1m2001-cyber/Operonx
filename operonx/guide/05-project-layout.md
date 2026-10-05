# 5. Project layout and conventions

A product built on operonx looks like this. The files below are a complete,
working project, and `operonx init` writes this layout (with a feature of
its own) for you.

```text
scorer/
├── operonx.toml          # where the application is; what the CLIs load
├── resources.yaml        # models, stores, tracing — by key, secrets as ${VAR}
├── .env                  # the secrets themselves; never committed
├── pyproject.toml        # operonx[extras] as a dependency; pytest's paths
├── AGENTS.md             # for coding assistants (CLAUDE.md holds `@AGENTS.md`)
├── .operonx/guide/       # this guide, copied by `operonx guide --sync`
├── app/
│   ├── __init__.py
│   └── main.py           # APP = Application(...): read this file first
├── src/
│   └── scoring/          # one folder per feature
│       ├── __init__.py
│       ├── graph.py      # @graph functions: wiring only
│       ├── ops.py        # @op functions: the logic
│       └── _text.py      # private helpers (leading underscore)
├── tests/
│   └── test_scoring.py
└── datasets/             # eval cases, one .jsonl per dataset
```

## The rules

- **`ops.py` holds the logic, `graph.py` only wires it.** A graph function
  creates ops, reads their outputs and draws `>>` edges. No computation,
  no I/O, no conditionals in Python: branch with `if_()`.
- **Imports go one way:** `app/` → `src/<feature>/graph.py` → `ops.py` →
  `_helpers.py`. Nothing in `src/` imports `app/`.
- **`app/main.py` is the map of the product:** every service and job,
  with the graph each runs. Door hooks (`on_session`) live beside it.
- **Resources by key.** Ops reach models and stores through
  `resources.yaml` keys (`LLMOp.of(resource="assistant")`), never by
  building clients. Secrets are `${VAR}` there and live in `.env`.
- **Name graphs and ops for what they do**, and pin with `name=` any name
  that other code reads (state keys, trace filters).
- **One feature, one folder.** Share code between features through a
  plain module, not by importing another feature's graph.
- **Test the ops directly** (`score(call=...)()`), and the graphs by
  running them with `Operon`.

## The files

```toml file=operonx.toml
[project]
name = "scorer"
src  = ["src", "."]     # import roots: `scoring` and `app` import from here
app  = "app.main:APP"

[resources]
overlay = "resources.yaml"

[tracing]                 # where every service's and job's runs are recorded
sinks = ["local"]         # e.g. ["trace_clickhouse:default"]: a resources.yaml key
```

```yaml file=resources.yaml
# models, stores and trace sinks by key; secrets as ${VAR} from .env
```

```toml file=pyproject.toml
[project]
name = "scorer"
version = "0.1.0"
requires-python = ">=3.10"
dependencies = ["operonx[serve]>=1.13"]

[tool.pytest.ini_options]
pythonpath = ["src", "."]
```

```python file=src/scoring/__init__.py
```

```python file=src/scoring/_text.py
def word_count(text: str) -> int:
    return len(text.split())
```

```python file=src/scoring/ops.py
from operonx import op

from scoring._text import word_count


@op
def score(call: dict) -> dict:
    words = word_count(call["text"])
    return {
        "result": {
            "id": call["id"],
            "words": words,
            "verdict": "engaged" if words >= 5 else "brief",
        }
    }
```

```python file=src/scoring/graph.py
from operonx import END, START, graph
from operonx.app.serve import egress, ingress

from scoring.ops import score


@graph
def score_flow():
    src = ingress()
    s = score(call=src["item"])
    out = egress(item=s["result"])
    START >> src >> s >> out >> END
```

```python file=app/__init__.py
```

```python file=app/main.py
"""The scorer: one HTTP service and the batch job over the same graph.

POST /score:8017 ──► score_flow ──► {id, words, verdict}
operonx run score_calls ──► score_flow per line of calls.jsonl ──► out/scored.jsonl
"""

from operonx.app import Application, Service, env, http
from operonx.app.jobs import Job

from scoring.graph import score_flow

APP = Application(
    "scorer",
    services=[Service("score", http("POST", "/score", port=env("PORT", 8017)), graph=score_flow)],
    jobs=[
        Job(
            "score_calls", graph=score_flow, source="calls.jsonl", sink="out/scored.jsonl", key="id"
        )
    ],
)
```

```json file=calls.jsonl
{"id": "c1", "text": "yes I can talk now"}
{"id": "c2", "text": "busy"}
```

```python file=tests/test_scoring.py
import asyncio

from operonx import Operon
from operonx.app.jobs import Job

from scoring.graph import score_flow
from scoring.ops import score


def test_score_counts_words():
    assert score(call={"id": "a", "text": "one two"})()["result"]["words"] == 2


def test_the_flow_scores_every_call():
    got = []
    run = asyncio.run(
        Job(
            "t",
            graph=score_flow,
            source=[{"id": "a", "text": "hi"}],
            sink=got,
            key="id",
            record_dir="out/test-jobs",
        ).run()
    )
    assert run.status == "ok" and got == [{"id": "a", "words": 1, "verdict": "brief"}]
```

```bash run
python -m pytest tests -q
operonx serve --list
operonx run score_calls
```

## Growing it

- **A second feature** is a second folder under `src/` with its own
  `graph.py` and `ops.py`, and one more entry in `app/main.py`.
- **A model** is an `llm:` entry in `resources.yaml` and an
  `LLMOp.of(resource=...)` in the feature's `graph.py`; its key goes in `.env`.
  Its provider's limits go on the entry, for the whole process:
  `rate_limit: {concurrency: 8, per_minute: 500}` (or `per_second`) — every
  op and run calling that key waits its turn; a stream holds its slot until
  it ends.
- **An eval** is a `datasets/<name>.jsonl` and an `Eval(...)` (a kind of
  job) in `app/main.py`; `operonx run <eval>` gates CI.
- **Door hooks** (`on_session`, `on_close`) go in `app/`, beside `main.py`.
