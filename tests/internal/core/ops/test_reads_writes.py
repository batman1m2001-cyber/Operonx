"""`@op(reads=, writes=)`: the stores an op declares it reads and writes."""

import pytest

from operonx import END, START, graph, op


@op(writes="inbox")
def keep(rows: list):
    return {"kept": len(rows)}


@op(reads=["inbox", "events", "inbox"])
def feed(n: int):
    return {"items": n}


@op
def plain(x: int):
    return {"y": x}


@graph
def declared_flow(rows=None):
    kept = keep(rows=rows)
    shown = feed(n=kept["kept"])
    late = plain(x=shown["items"], writes=" audit ")
    START >> kept >> shown >> late >> END


def test_declared_on_the_decorator_and_at_the_call():
    g = declared_flow(rows=[1, 2])
    g.build()
    by = dict(g._ops)
    assert by["kept"].writes == ("inbox",) and by["kept"].reads == ()
    assert by["shown"].reads == ("inbox", "events")  # in order, once each
    assert by["late"].writes == ("audit",) and by["late"].reads == ()


def test_a_store_name_is_a_string():
    with pytest.raises(TypeError, match="reads takes a store name"):
        plain(x=1, reads=[3])
    with pytest.raises(TypeError, match="writes takes a store name"):
        plain(x=1, writes="")


def test_a_parameter_of_that_name_stays_an_input():
    @op
    def takes(reads: int):
        return {"n": reads}

    o = takes(reads=4)
    assert o.reads == ()
