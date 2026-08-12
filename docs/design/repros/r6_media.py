"""R6: BaseOp.get_inputs fast path never unwraps Media."""
import asyncio

import operonx

print("source:", operonx.__file__)
from operonx.core import END, PARENT, START, GraphOp, Operon, op
from operonx.core.media import Media

seen = []

@op
def make(n: int):
    for i in range(3):
        yield {"blob": Media(data=b"x" * (i + 1), mime_type="application/octet-stream")}

@op
def use(blob):
    seen.append(type(blob).__name__)
    return {"kind": type(blob).__name__}

with GraphOp(name="media_g") as g:
    m = make(n=PARENT["n"])
    u = use(blob=m["blob"].parallel())
    START >> m >> u >> END

async def main():
    out = await Operon(g).run(inputs={"n": 3})
    print("per-item input type:", seen)
    print("out:", out.get("kind"))

asyncio.run(main())
