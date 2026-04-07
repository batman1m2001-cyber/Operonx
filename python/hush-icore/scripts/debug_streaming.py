"""Debug streaming: source yields → double → END."""

import asyncio
import os

os.environ["LOG_LEVEL"] = "DEBUG"

from hush.core import END, PARENT, START, GraphOp, op
from hush.core.states import StateSchema


@op
def source(items: list):
    for item in items:
        yield {"x": item}


@op
def double(x: int):
    return {"result": x * 2}


with GraphOp(name="test") as g:
    s = source(items=PARENT["items"])
    d = double(x=s["x"])
    START >> s >> d >> END

g.build()
schema = StateSchema(g)

print("Schema vars:")
for (op_name, var), idx in sorted(schema._var_to_idx.items()):
    print(f"  ({op_name}, {var}) = idx {idx}")

state = schema.create_state(inputs={"items": [1, 2, 3]})
result = asyncio.run(g.run(state))
print(f"\nRESULT: {result}")
