"""A misspelled output key is a build error (roadmap C7).

``show(total=m["totl"])`` built, ran, and handed ``show`` its default:
``total=0``, no ``$errors`` (``docs/roadmap/evidence/repros/typo_key.py``).
A misspelled *input* name was already a ``TypeError`` at the call site;
the output side is checked when the graph is built, against the keys of
the dict literals the producer returns. A producer whose outputs are not
known before it runs is not checked.
"""

import pytest

from operonx import END, START, Operon, graph, op
from operonx.core.ops import if_
from operonx.core.ops.graph import GraphValidationError


@op
def make(x: int) -> dict:
    return {"total": x + 1}


@op
def show(total: int = 0) -> dict:
    return {"text": f"total={total}"}


@graph
def typo_output(x):
    m = make(x=x)
    s = show(total=m["totl"])  # typo in the output key
    START >> m >> s >> END


def test_unknown_output_key():
    with pytest.raises(GraphValidationError) as exc_info:
        Operon(typo_output, params={"x": None})

    message = str(exc_info.value)
    assert "'totl' is not an output of make()" in message
    assert "did you mean 'total'?" in message
    assert "show()" in message  # names the op that reads it


@graph
def branchy(x):
    m = make(x=x)
    s = show(total=m["total"])
    START >> m >> if_(m["totl"] > 3, s).else_(END)
    s >> END


def test_misspelled_key_inside_a_condition():
    with pytest.raises(GraphValidationError, match="'totl' is not an output of make()"):
        Operon(branchy, params={"x": None})


@graph
def fine(x):
    m = make(x=x)
    s = show(total=m["total"])
    e = show(total=m["error"])  # every op has an error output
    START >> m >> [s, e] >> END


async def test_known_outputs_and_metadata_still_build():
    out = await Operon(fine, params={"x": None}).run(inputs={"x": 1})
    assert not out.get("$errors")


@op
def dynamic(x: int) -> dict:
    result = {}
    result[f"k{x}"] = x
    return result


@op
def mixed(x: int) -> dict:
    if x:
        return {"a": 1}
    return dict(b=2)


@op
def declared(x: int):
    return {"declared": x, "extra": x}


@graph
def g(x):
    d = dynamic(x=x)
    m = mixed(x=x)
    r = declared(x=x, return_keys=["declared"])
    s1 = show(total=d["k1"])
    s2 = show(total=m["b"])
    s3 = show(total=r["extra"])
    START >> [d, m, r] >> s1 >> s2 >> s3 >> END


async def test_dynamic_outputs_are_not_checked():
    out = await Operon(g, params={"x": None}).run(inputs={"x": 1})
    assert not out.get("$errors")


@op
def shown(total: int = 0, note: str = "none") -> dict:
    return {"line": f"{total}/{note}"}


@graph
def g_get_reads_an_optional_output_with_the_readers_default(x):
    m = make(x=x)
    s = shown(total=m["total"], note=m.get("note"))
    START >> m >> s >> END


async def test_get_reads_an_optional_output_with_the_readers_default():
    """`op.get("key")`: not checked at build, the reader's default at run."""

    out = await Operon(
        g_get_reads_an_optional_output_with_the_readers_default, params={"x": None}
    ).run(inputs={"x": 1})
    assert out["line"] == "2/none"


@graph
def transformed(x):
    m = make(x=x)
    s = show(total=m.get("bonus") + 1)  # optional through a transform
    START >> m >> s >> END


@graph
def mixed_get_survives_transforms_and_a_plain_read_is_still_checked(x):
    m = make(x=x)
    s = show(total=m.get("totl") + m["totl"])  # read plainly too: checked
    START >> m >> s >> END


def test_get_survives_transforms_and_a_plain_read_is_still_checked():
    Operon(transformed, params={"x": None})  # builds

    with pytest.raises(GraphValidationError, match="did you mean 'total'"):
        Operon(mixed_get_survives_transforms_and_a_plain_read_is_still_checked, params={"x": None})
