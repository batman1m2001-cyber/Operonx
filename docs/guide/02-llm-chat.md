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

`prompt=` accepts three shapes:

* **str** — becomes a single user message.
* **dict** with `system` / `user` keys — 1-2 messages with `{var}` placeholders.
* **list** — a full OpenAI messages array (multimodal blocks supported).

Every non-reserved kwarg is a template variable substituted into any
`{var}` placeholder inside `prompt`. The output key is `content` by default.

## Passing a pre-built messages list

When you already have a list of messages (e.g. multi-turn conversation),
pass it as `prompt=`:

```python
llm = LLMOp.of(resource="gpt-4o", prompt=PARENT["messages"])
START >> llm >> END
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
