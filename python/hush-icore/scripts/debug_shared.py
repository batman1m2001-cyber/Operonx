"""Debug shared vars."""

import asyncio

from hush.core import END, PARENT, START, GraphOp, op
from hush.core.states import StateSchema


@op
def source(n: int):
    for i in range(n):
        yield {"idx": i}


@op
def increment(idx: int, counter: int):
    return {"new_counter": counter + 1, "seen_idx": idx}


with GraphOp(name="shared_test") as g:
    PARENT.shared(counter=0)

    s = source(n=PARENT["n"])
    inc = increment(idx=s["idx"], counter=PARENT["counter"])
    inc["new_counter"] >> PARENT["counter"]
    START >> s >> inc >> END

g.build()

print("_shared_vars:", g._shared_vars)
schema = StateSchema(g)
print("_shared_indices:", schema._shared_indices)
for (op_name, var), idx in sorted(schema._var_to_idx.items()):
    shared = "SHARED" if idx in schema._shared_indices else ""
    print(f"  ({op_name}, {var}) idx={idx} {shared}")

state = schema.create_state(inputs={"n": 3})
result = asyncio.run(g.run(state))

print("\nResult:", result)
print("Counter at DEFAULT:", state["shared_test", "counter", ("main",)])
