"""Debug: shared var mutable object — same reference across requests?"""

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
    return {"new_buffer": new_buffer}


# Build graph ONCE
with GraphOp(name="test") as g:
    PARENT.shared(buffer=[])
    s = source(n=PARENT["n"])
    a = accumulate(x=s["x"], buffer=PARENT["buffer"])
    a["new_buffer"] >> PARENT["buffer"]
    START >> s >> a >> END

g.build()
schema = StateSchema(g)


async def run_request(request_id, n):
    """Simulate 1 request."""
    state = schema.create_state(inputs={"n": n})
    result = await g.run(state)
    buffer = result["buffer"]
    return request_id, buffer


async def main():
    # Request 1: accumulate [0, 1, 2]
    _, buf1 = await run_request("req1", 3)
    print(f"Request 1: buffer = {buf1}")

    # Request 2: accumulate [0, 1] — should be fresh, NOT [0,1,2,0,1]
    _, buf2 = await run_request("req2", 2)
    print(f"Request 2: buffer = {buf2}")

    # Request 3: accumulate [0]
    _, buf3 = await run_request("req3", 1)
    print(f"Request 3: buffer = {buf3}")

    # Check
    if buf1 == [0, 1, 2] and buf2 == [0, 1] and buf3 == [0]:
        print("\n✓ CORRECT — each request has fresh state")
    else:
        print(f"\n✗ BUG — state leaking between requests")
        print(f"  Expected: [0,1,2], [0,1], [0]")
        print(f"  Got:      {buf1}, {buf2}, {buf3}")


asyncio.run(main())
