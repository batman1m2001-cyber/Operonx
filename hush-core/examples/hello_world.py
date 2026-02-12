import asyncio
import time

from hush.core import END, PARENT, START, FuncOp, GraphOp, Hush
from hush.core.tracers import LocalTracer


async def main():
    with GraphOp(name="my-workflow-2") as graph:
        step1 = FuncOp(
            name="fetch", code_fn=lambda: {"data": [1, 2, 3, 4, 5]}, outputs={"data": PARENT}
        )
        step2 = FuncOp(
            name="transform",
            code_fn=lambda data: {"result": sum(data)},
            inputs={"data": PARENT["data"]},
            outputs={"result": PARENT},
        )
        START >> step1 >> step2 >> END

    engine = Hush(graph)
    result = await engine.run(inputs={}, tracer=LocalTracer())
    print(result["result"])  # 15
    time.sleep(5)


if __name__ == "__main__":
    asyncio.run(main())
