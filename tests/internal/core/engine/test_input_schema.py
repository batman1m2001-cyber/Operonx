"""`Operon.input_schema()` describes what a run takes.

It read `param.annotation`, which `Param` never had, so every call raised
`AttributeError`. It reads `Param.type` now, and a served graph's
signature defaults (`inputs_defaults`) make those parameters optional.
"""

from __future__ import annotations

from operonx.app.serve.app import compile_graph
from operonx.core import END, START, Operon, graph, op


@op
def echo(x):
    return {"y": x}


@graph
def greet(who, limit=10):
    said = echo(x=who)
    START >> said >> END


def test_a_served_graph_has_a_schema_with_its_defaults():
    schema = compile_graph(greet).input_schema()
    assert schema["title"] == "greet_input"
    assert set(schema["properties"]) == {"who", "limit"}
    assert schema["required"] == ["who"]
    assert schema["properties"]["limit"]["default"] == 10


def test_a_graph_compiled_directly_still_answers():
    schema = Operon(greet(who="x")).input_schema()
    assert schema["type"] == "object"
    assert Operon(greet(who="x")).output_schema()["type"] == "object"
