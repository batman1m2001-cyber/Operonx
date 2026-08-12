# LLM chat

This guide adds a real LLM call to the workflow. Pre-requisites:

```bash
pip install "operonx[standard]"     # includes OpenAI provider
```

You will need:

- `OPENAI_API_KEY` in `.env`.
- A `resources.yaml` listing the model.

## Configure resources

`resources.yaml`:

```yaml
llms:
  gpt-4o:
    backend: openai
    model: gpt-4o
    api_key: ${OPENAI_API_KEY}
    temperature: 0.7
```

`.env`:

```
OPENAI_API_KEY=sk-...
```

## Use `LLMOp.of` for a one-shot prompt

`LLMOp` formats a prompt template and calls the model in one step. Use
`ask` (from `operonx.providers`) when you also want to parse structured
fields out of the reply and optionally retry on validation failure.

```python
import asyncio
import operonx
from operonx.core import Operon, GraphOp, START, END, PARENT
from operonx.providers import LLMOp

async def main():
    operonx.bootstrap()  # loads .env + resources.yaml

    with GraphOp(name="chat") as graph:
        c = LLMOp.of(
            resource="gpt-4o",
            prompt={
                "system": "You are a concise assistant.",
                "user": "{question}",
            },
            question=PARENT["question"],
        )
        START >> c >> END

    engine = Operon(graph)
    result = await engine.run(inputs={"question": "What is Python?"})
    print(result["content"])

asyncio.run(main())
```

`prompt=` is a **template**, in one of two shapes:

* **str** — becomes a single user message.
* **dict** with `system` / `user` keys — the standard two-message call.

Every non-reserved kwarg is a template variable substituted into any
`{var}` placeholder inside `prompt`. The output key is `content` by default.

## Passing a pre-built messages list

A conversation is data, not a template, so it goes to `messages=` — which
is never formatted:

```python
llm = LLMOp.of(resource="gpt-4o", messages=PARENT["messages"])
START >> llm >> END
```

The two are mutually exclusive, and one is required.

!!! warning "Do not pass a conversation to `prompt=`"
    `prompt=` formats what it is given. A message list is full of braces
    the formatter tries to resolve — a tool returning `{"city": "Hanoi"}`,
    a user pasting CSS, the model's own tool-call arguments — and each one
    becomes a template variable that does not exist. `prompt=` used to
    accept a list; it now raises and names `messages=`.

Multimodal content is a message list too, so it also goes through
`messages=`. Build it in an upstream op when the values are computed:

```python
@op
def build_vision_prompt(query: str, image_url: str) -> dict:
    return {"messages": [
        {"role": "user", "content": [
            {"type": "text", "text": f"Describe: {query}"},
            {"type": "image_url", "image_url": {"url": image_url}},
        ]},
    ]}

p = build_vision_prompt(query=PARENT["query"], image_url=upload["url"])
llm = LLMOp.of(resource="gpt-4o", messages=p["messages"])
```

## Structured output — `fields=`

Ask the model for a shape rather than prose, and have it parsed inline:

```python
classify = LLMOp.of(
    resource="gpt-4o",
    prompt={"system": "Classify the intent.", "user": "{utterance}"},
    fields=["intent: str", "confidence: float"],
    parser="xml",                       # or "json" / "yaml"
    validators={"intent": ["book", "cancel", "@unknown"]},
    max_retries=2,
    utterance=PARENT["utterance"],
)
# classify["intent"], classify["confidence"], classify["error"]
```

Each field becomes a top-level output. `error` is `None` on success and a
human-readable string otherwise — that is what `max_retries` reads to
decide whether to ask again.

Three rules worth knowing before you rely on it:

- **A missing field is an error, not a `None`.** That is what lets
  `max_retries` fire. A field the model explicitly set to `null` *is* an
  answer and is not an error.
- **Mark optional fields `"name?: type"`.** A *union schema* — one field
  list covering several response shapes, where most entries are absent on
  any given call — needs this on every entry that is not always present.
  Without it every call reports missing fields and burns its retries.
- **A `@`-prefixed validator value is a default.** When the model answers
  outside the allow-list, that value is substituted instead of erroring.

```python
fields=[
    "intent: str",          # always present
    "chosen_date?: str",    # only on booking turns
    "reason?: str",         # only on cancellations
]
```

## Streaming a response

`LLMOp` supports streaming. The op yields one frame per token chunk;
downstream ops consume them as they arrive.

```python
c = LLMOp.of(
    resource="gpt-4o",
    stream=True,
    prompt={"system": "...", "user": "{q}"},
    q=PARENT["q"],
)
```

See [Streaming](06-streaming.md) for the consumption side.

## Where to go next

- Multi-step agents: [Agents](05-agents.md).
- Add retrieval: [RAG](04-rag.md).
- Trace every call: [Tracing](07-tracing.md).
- What reaches the trace, and how to keep a credential out of it:
  [Observability](../architecture/observability.md).
