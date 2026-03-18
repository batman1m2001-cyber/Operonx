# Hush AI — Complete Vibe Coding Guide

Hush is a high-performance workflow engine for AI applications. This guide covers everything you need to generate correct Hush code.

## Setup

```python
import asyncio
from dotenv import load_dotenv
load_dotenv()
```

API keys go in `.env`. Resources (LLM, embedding, etc.) are defined in `resources.yaml` and referenced by name.

## Core Concepts (hush-icore)

- `@op` — a workflow step (function → dict)
- `GraphOp` — wires ops into a DAG
- `START >> op >> END` — execution order
- `PARENT["key"]` — external input (from `engine.run()` or parent graph)
- `op["key"]` — output from a sibling op
- `>> END` — auto-forwards last op's outputs as graph result

## Basic Workflow

```python
from hush.core import Hush, GraphOp, op, START, END, PARENT

@op
def double(x: int):
    return {"result": x * 2}

with GraphOp(name="workflow") as graph:
    d = double(x=PARENT["input"])
    START >> d >> END

async def main():
    engine = Hush(graph)
    result = await engine.run({"input": 5})
    print(result)  # {"result": 10}

asyncio.run(main())
```

## Data Flow Rules

```python
# PARENT["key"] — external inputs only
# op["key"] — sibling op outputs only. NEVER mix them.
g = greet(user=PARENT["user"])       # from engine.run({"user": ...})
u = upper(text=g["greeting"])        # from sibling op g
START >> g >> u >> END
```

## Output Mapping

```python
# Auto-forward: >> END forwards last op's outputs
START >> step >> END  # step's outputs become graph result

# Explicit mapping
llm = LLMOp.of(resource="gpt-4o", messages=p["messages"],
                outputs={"content": PARENT["answer"]})
# Or standalone
llm["content"] >> PARENT["answer"]

# Wildcard — forward all outputs
step = process(x=PARENT["x"], outputs={"*": PARENT})
```

## @op Decorator

```python
# Default — runs on event loop
@op
def process(text: str):
    return {"result": text.upper()}

# Blocking I/O — use thread executor
@op(executor="thread")
def fetch(url: str):
    return {"data": requests.get(url).json()}

# Async
@op(bound="io")
async def call_api(url: str):
    async with aiohttp.ClientSession() as s:
        async with s.get(url) as resp:
            return {"data": await resp.json()}

# CPU-bound (heavy compute)
@op(bound="cpu")
def heavy(items: list):
    return {"result": [expensive(x) for x in items]}
```

## Graph Patterns

```python
# Linear chain
START >> a >> b >> c >> END

# Parallel fan-out + merge
START >> [a, b] >> merge >> END

# Nested graph
with GraphOp(name="outer") as main:
    inner = sub_workflow(text=PARENT["input"])
    START >> inner >> END
```

## Generators (replaces ForOp/MapOp)

```python
@op
def each_item(items: list):
    for item in items:
        yield {"value": item}  # each yield = parallel stream

@op
def process(value: str):
    return {"result": value.upper()}

with GraphOp(name="iterate") as graph:
    gen = each_item(items=PARENT["items"])
    step = process(value=gen["value"])
    START >> gen >> step >> END
# Downstream ops run in parallel per yield
```

## Loops (replaces WhileOp)

```python
with GraphOp.loop(until="count >= 5", count=0) as loop:
    inc = increment(counter=PARENT["count"])
    inc["counter"] >> PARENT["count"]  # feed back to loop state
    START >> inc >> END
```

## @graph Decorator (reusable factories)

```python
from hush.core import graph

@graph
def classify(text, model):
    p = PromptOp.of(template="Classify: {text}", text=text)
    llm = LLMOp.of(resource=model, messages=p["messages"])
    START >> p >> llm >> END

# Usage — auto-named from variable
with GraphOp(name="main") as g:
    c1 = classify(text=PARENT["doc1"], model="gpt-4o")
    c2 = classify(text=PARENT["doc2"], model="gpt-4o")
    START >> [c1, c2] >> END

# Loop variant
@graph.loop(until="done == true", max_iterations=10)
def agent_loop(messages, done):
    step = agent_step(messages=PARENT["messages"])
    step["messages"] >> PARENT["messages"]
    step["done"] >> PARENT["done"]
    START >> step >> END
```

## Branching (BranchOp)

```python
from hush.core import BranchOp
from hush.core.ops.flow import if_

# Fluent API
router = (
    if_(PARENT["score"] >= 90, "excellent")
    .if_(PARENT["score"] >= 70, "good")
    .else_("fail")
)

with GraphOp(name="grade") as graph:
    s = get_score(input=PARENT["input"])
    router = if_(s["score"] >= 90, "ex").if_(s["score"] >= 70, "ok").else_("fail")
    ex = handle_excellent(score=s["score"])
    ok = handle_good(score=s["score"])
    fail = handle_fail(score=s["score"])

    START >> s >> router
    router >>~ ex >> END      # >>~ is soft edge (only runs if selected)
    router >>~ ok >> END
    router >>~ fail >> END

# Conditions support: >=, >, ==, !=, <, <=, & (and), | (or)
```

## Provider Ops (hush-providers)

### LLMOp

```python
from hush.providers import LLMOp

llm = LLMOp.of(
    resource="gpt-4o",               # from resources.yaml
    messages=p["messages"],           # OpenAI message format
    temperature=0.7,                  # optional
    max_tokens=1000,                  # optional
    tools=[...],                      # function calling
    response_format={"type": "json_object"},  # JSON mode
)
# Outputs: content, role, finish_reason, model_used, tokens_used, tool_calls

# Load balancing
llm = LLMOp.of(
    resource=["gpt-4o", "gpt-4o-mini"],
    ratios=[0.7, 0.3],
    fallback=["gpt-3.5-turbo"],
    messages=p["messages"],
)
```

### PromptOp

```python
from hush.providers import PromptOp

# Dict template (most common)
p = PromptOp.of(
    template={"system": "You are {role}.", "user": "{query}"},
    role="helpful",
    query=PARENT["query"],
)
# Output: messages (list[dict] in OpenAI format)

# String template → single user message
p = PromptOp.of(template="Hello {name}", name=PARENT["name"])

# With conversation history
p = PromptOp.of(
    template={"system": "...", "user": "{query}"},
    conversation_history=PARENT["history"],  # inserted before last user msg
    query=PARENT["query"],
)
```

### EmbeddingOp

```python
from hush.providers import EmbeddingOp

e = EmbeddingOp.of(resource="openai", texts=PARENT["texts"])
# Output: embeddings (list[list[float]])
```

### RerankOp

```python
from hush.providers import RerankOp

r = RerankOp.of(
    resource="pinecone",
    query=PARENT["query"],
    documents=PARENT["docs"],
    top_k=5,
)
# Output: reranks (list[dict] with index, score, document)
```

### chain() — Prompt + LLM + Parser in one

```python
from hush.providers import chain

# Simple chat
chat = chain(
    resource="gpt-4o",
    template={"system": "You are helpful.", "user": "{query}"},
    query=PARENT["query"],
)
# Output: content, role, finish_reason, model_used, tokens_used

# Structured extraction
parsed = chain(
    resource="gpt-4o",
    template="Classify this text: {text}",
    extract=["category: str", "confidence: float"],
    parser="xml",           # xml, json, or yaml
    text=PARENT["text"],
)
# Output: category, confidence (extracted fields)

# JSON mode
json_out = chain(
    resource="gpt-4o",
    template="List 3 facts about {topic}",
    response_format={"type": "json_object"},
    topic=PARENT["topic"],
)
```

### ParserOp

```python
from hush.core import ParserOp

parser = ParserOp(
    format="json",                    # json, xml, or yaml
    extract=[
        "action: str",
        "params: dict",
        "confidence: float",
    ],
    inputs={"text": llm["content"]},
)
# Handles code blocks (```json ... ```) and raw text
# Type conversion: str, int, float, bool, dict, list, any
```

## Telemetry (hush-telemetry)

```python
from hush.telemetry import LangfuseTracer, HushEyesTracer, OTELTracer

# Langfuse tracing
tracer = LangfuseTracer(resource="langfuse:default", tags=["prod"])
engine = Hush(graph, tracer=tracer)

# Multiple tracers
engine = Hush(graph, tracer=[
    HushEyesTracer(),                              # local UI
    LangfuseTracer(resource="langfuse:default"),   # cloud
])

# Dynamic tags from ops
@op
def cached_fetch(key: str):
    if key in cache:
        return {"result": cache[key], "$tags": ["cache-hit"]}
    return {"result": fetch(key), "$tags": ["cache-miss"]}
```

## Serving (hush-serve)

```python
from hush.serve import HushApp
from hush.core import graph, START, END
from hush.providers import chain

app = HushApp(title="My API", resources="resources.yaml")

# Decorator style
@app.endpoint("/chat", stream=True, tags=["llm"])
@graph
def chat(query):
    c = chain(resource="gpt-4o", template={"user": "{query}"}, query=query)
    START >> c >> END

# Method style
app.endpoint("/score", graph=scoring_graph)

# Serve
app.serve(port=8000)                      # Python (FastAPI + uvicorn)
app.serve(port=8000, backend="rust")      # Rust (Axum, ~10x faster)

# Generated routes per endpoint:
#   POST /chat          — sync JSON
#   POST /chat/stream   — Server-Sent Events
#   GET  /health        — health check
#   GET  /endpoints     — endpoint metadata
```

## Exception Hierarchy

All op errors inherit from `OpError`:
- `CodeError` — @op function failed (includes function_name, inputs, source)
- `ParserError` — parse failed (includes input_text, format_type)
- `BranchError` — condition eval failed (includes condition, candidates)
- `PromptError` — template error (includes template, missing_vars)
- `EmbeddingError` — embedding failed (includes resource, text_count)
- `RerankError` — rerank failed (includes resource, query, document_count)

```python
from hush.core.exceptions import OpError, CodeError

try:
    result = await engine.run(inputs)
except CodeError as e:
    print(f"Function {e.function_name} failed: {e.message}")
except OpError as e:
    print(f"[{e.op_type}] {e.message}")
```

## Rules

1. Always use **keyword arguments** — never positional
2. Use `Op.of()` classmethods: `LLMOp.of()`, `EmbeddingOp.of()`, `PromptOp.of()`
3. `PARENT["key"]` for external inputs, `op["key"]` for sibling outputs — never mix
4. `>> END` auto-forwards outputs — no manual mapping needed for simple cases
5. Resources defined in `resources.yaml`, referenced by name: `resource="gpt-4o"`
6. API keys in `.env`, referenced in resources.yaml as `${VAR_NAME}`
7. Always `from dotenv import load_dotenv; load_dotenv()` at top of scripts
8. `engine.run()` is async — use `await` or `asyncio.run()`
9. `>>~` for soft edges (branch targets) — only selected branch runs
10. Generators use `yield` in `@op` — downstream runs per yield in parallel
