"""Reproduce: shared var push ref wraps value in list."""

import asyncio
from collections import deque

from hush.core import END, PARENT, START, GraphOp, op
from hush.core.states import StateSchema


@op
def source(n: int):
    for i in range(n):
        yield {"x": i}


@op
def accumulate(x: int, buffer: list):
    new_buffer = list(buffer) + [x]
    return {"new_buffer": new_buffer, "count": len(new_buffer)}


with GraphOp(name="test") as g:
    PARENT.shared(buffer=[])

    s = source(n=PARENT["n"])
    a = accumulate(x=s["x"], buffer=PARENT["buffer"])
    a["new_buffer"] >> PARENT["buffer"]
    START >> s >> a >> END

g.build()
schema = StateSchema(g)
state = schema.create_state(inputs={"n": 3})
result = asyncio.run(g.run(state))

print("Result keys:", list(result.keys()))
print()

# Check shared var
buffer_val = state["test", "buffer", ("main",)]
print(f"buffer type: {type(buffer_val).__name__}")
print(f"buffer value: {buffer_val}")
print()

# Expected: [0, 1, 2]
# Bug: might be [[0], [0,1], [0,1,2]] or other nesting
if buffer_val == [0, 1, 2]:
    print("✓ CORRECT")
else:
    print(f"✗ WRONG — expected [0, 1, 2]")
