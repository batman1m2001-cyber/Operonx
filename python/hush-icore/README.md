# hush-icore

Core workflow engine for Hush — async DAG execution with built-in tracing and type-safe state management.

[![PyPI](https://img.shields.io/pypi/v/hush-icore)](https://pypi.org/project/hush-icore/)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://python.org)

## Installation

```bash
pip install hush-icore
```

## Quick Start

```python
import asyncio
from hush.core import Hush, GraphOp, op, START, END, PARENT

@op
def double(x: int):
    return {"result": x * 2}

async def main():
    with GraphOp(name="workflow") as graph:
        step = double(x=PARENT["x"])
        START >> step >> END

    result = await Hush(graph).run(inputs={"x": 5})
    print(result["result"])  # 10

asyncio.run(main())
```

## Key Concepts

### Ops

- **`@op`** — Turn any function into a workflow node
- **`GraphOp`** — Container for nested workflows
- **`BranchOp`** — Conditional routing (`if_(...).else_(...)`)
- **`@graph`** — Reusable workflow factory with auto-naming


### @graph — Reusable Workflow Factory

```python
from hush.core import graph, op, START, END, PARENT, GraphOp, Hush

#        fetch
#       /     \
# summarize  translate    (parallel)
#       \     /
#     merge_results

@op
def fetch(url: str):
    return {"data": f"content from {url}"}

@op
def summarize(text: str):
    return {"summary": text[:50] + "..."}

@op
def translate(text: str):
    return {"translated": f"[VI] {text}"}

@op
def merge_results(summary: str, translated: str):
    return {"report": f"{summary}\n{translated}"}

@graph
def process_url(url):
    f = fetch(url=url)
    s = summarize(text=f["data"])
    t = translate(text=f["data"])
    m = merge_results(summary=s["summary"], translated=t["translated"])
    START >> f >> [s, t] >> m >> END

@op
def publish(report: str):
    return {"status": f"published: {report[:30]}..."}

# Use in a parent graph — auto-named from variable
async def main():
    with GraphOp(name="pipeline") as pipeline:
        p = process_url(url=PARENT["url"])
        pub = publish(report=p["report"])
        START >> p >> pub >> END

    result = await Hush(pipeline).run(inputs={"url": "https://example.com"})
    print(result["status"])
```
### Flow Control

```python
# Sequential
START >> a >> b >> c >> END

# Parallel (fork + join)
START >> [a, b, c] >> merge >> END

# Generator (streaming iteration)
@op
def each_item(items: list):
    for item in items:
        yield {"value": item}

# Loop (feedback loop with condition)
with GraphOp.loop(until="count >= 5") as loop:
    inc = increment(counter=PARENT["count"])
    inc["counter"] >> PARENT["count"]
    START >> inc >> END
```

### State References

```python
# PARENT["key"] — external inputs from engine.run()
step = double(x=PARENT["input"])

# op["key"] — output from sibling op
upper = to_upper(text=greet["greeting"])

# Output mapping
step["result"] >> PARENT["answer"]
```

### Tracing

```python
from hush.core.tracing import LocalTracer

engine = Hush(graph, tracer=LocalTracer(tags=["dev"]))
result = await engine.run(inputs={...})
# Traces written to ~/.hush/traces/{request_id}.json
```

For external backends (Langfuse, OpenTelemetry), see [hush-telemetry](https://pypi.org/project/hush-telemetry/).

## Rust Backend

hush-icore has a companion [Rust crate](https://crates.io/crates/hush-icore) for high-performance execution (~8x faster on pure-compute). Use via [hush-serve](https://pypi.org/project/hush-serve/):

```python
engine = Hush(graph)
engine.serve(port=8000, backend="rust")
```

## Related Packages

| Package | Description |
|---------|-------------|
| [hush-providers](https://pypi.org/project/hush-providers/) | LLM, embedding, reranking integrations |
| [hush-telemetry](https://pypi.org/project/hush-telemetry/) | Langfuse, OpenTelemetry tracing |
| [hush-serve](https://pypi.org/project/hush-serve/) | HTTP API server (Python + Rust backends) |

## License

Apache 2.0
