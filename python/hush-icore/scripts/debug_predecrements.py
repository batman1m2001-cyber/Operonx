"""Debug _stream_predecrements."""

from hush.core.ops.graph.scheduler import _is_gen

from hush.core import END, PARENT, START, GraphOp, op


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

# Before build
print("Before build:")
for name, child in g._ops.items():
    print(f"  {name}: _is_gen={_is_gen(child)}, core={child.core}")

g.build()

print("\nAfter build:")
print("  initial_ready_count:", dict(g.initial_ready_count))
print("  _stream_predecrements:", dict(g._stream_predecrements))
print("  gen_names:", {name for name, op in g._ops.items() if _is_gen(op)})
print("  nexts:", {k: v for k, v in g.nexts.items()})
