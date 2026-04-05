"""Basic example of using LangfuseTracer with the Hush engine.

Traces are collected automatically after the workflow completes — you never
add spans or generations manually. The engine calls FlushWorker which runs
TraceCollector to build a TraceNode tree, then POSTs it to Langfuse.

Setup: copy env.example → .env and fill in LANGFUSE_* keys, or set:
    LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY, LANGFUSE_HOST
"""

import asyncio

from hush.core import END, PARENT, START, GraphOp, Hush, op

from hush.telemetry import LangfuseTracer
from hush.telemetry.backends.langfuse import LangfuseConfig


@op
def greet(name: str):
    return {"message": f"Hello, {name}!"}


@op
def shout(message: str):
    return {"result": message.upper()}


def build_graph() -> GraphOp:
    with GraphOp(name="greeting_workflow") as graph:
        g = greet(name=PARENT["name"])
        s = shout(message=g["message"])
        START >> g >> s >> END
    return graph


async def main():
    graph = build_graph()

    # Option 1: Direct config from environment variables
    tracer = LangfuseTracer(config=LangfuseConfig.from_env())

    # Option 2: From ResourceHub (requires resources.yaml with langfuse:default)
    # from hush.core.registry import get_hub
    # import hush.telemetry  # noqa: F401  — registers ObservabilityPlugin
    # tracer = LangfuseTracer(resource="langfuse:default", tags=["example"])

    engine = Hush(graph, tracer=tracer)

    # engine.run() executes the workflow and flushes traces to Langfuse automatically.
    # No manual span/generation/event calls — tracing is fully automatic.
    result = await engine.run(inputs={"name": "Hush"})
    print("Result:", result["result"])

    # engine.start() returns an ExecutionHandle for streaming.
    # Traces still flush automatically when the handle is fully consumed.
    handle = engine.start(inputs={"name": "World"})
    async for op_name, _ctx, data in handle:
        print(f"  frame: {op_name} → {data}")
    final = await handle.collect()
    print("Final:", final["result"])


if __name__ == "__main__":
    asyncio.run(main())
