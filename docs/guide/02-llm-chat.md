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

## Use `chat` for a one-shot prompt

`chat` is a shorthand that combines a prompt template with an LLM call.
Use `ask` when you want a single-string answer instead of a structured
chat response.

```python
import asyncio
import operonx
from operonx.core import Operon, GraphOp, START, END, PARENT
from operonx.providers import chat

async def main():
    operonx.bootstrap()  # loads .env + resources.yaml

    with GraphOp(name="chat") as graph:
        c = chat(
            resource="gpt-4o",
            template={
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

`chat` always uses keyword arguments — never positional. The output
key is `content` by default. Map it explicitly with `outputs={...}` if
you want a different name.

## Use `LLMOp.of` for structured calls

When you already have a list of messages (e.g. multi-turn conversation),
use `LLMOp.of` directly:

```python
from operonx.providers import LLMOp

llm = LLMOp.of(resource="gpt-4o", messages=PARENT["messages"])
START >> llm >> END
```

`messages` is a list of `{"role": "system" | "user" | "assistant", "content": ...}`
dicts.

## Streaming a response

`LLMOp` and `chat` both support streaming. The op yields one frame per
token chunk; downstream ops consume them as they arrive.

```python
c = chat(resource="gpt-4o", template={...}, stream=True, question=PARENT["q"])
```

See [Streaming](06-streaming.md) for the consumption side.

## Where to go next

- Multi-step agents: [Agents](05-agents.md).
- Add retrieval: [RAG](04-rag.md).
- Trace every call: [Tracing](07-tracing.md).
