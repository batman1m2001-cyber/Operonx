"""Shared `@op` pool used by every spec fixture's `builder.py`.

The Python and Rust parity harnesses read the same fixture JSON from this
directory tree. On the Python side, fixtures build a `GraphOp` via the
authoring DSL and run it through `Operon`; on the Rust side the serialized
`graph.json` feeds the engine directly. The op names here must match the
`func_name` strings the Rust-side shared pool registers in
`rust/operonx/tests/common/mod.rs`.
"""

from operon.core import op


@op
def double(x: int):
    return {"result": x * 2}


@op
def add_one(n: int):
    return {"answer": n + 1}


@op
def echo(msg):
    return {"output": msg}


@op
def wrap_user(text: str):
    return {"msg": {"role": "user", "content": text}}


@op
def classify_size(n: int, threshold: int):
    return {"label": "big" if n > threshold else "small"}


@op
def lowercase(text: str):
    return {"result": text.lower()}


@op
def sum_list(xs):
    return {"total": sum(xs)}


@op
def increment(counter: int):
    return {"counter": counter + 1}
