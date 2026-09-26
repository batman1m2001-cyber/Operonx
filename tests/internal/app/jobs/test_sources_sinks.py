"""Sources and sinks: files, Python, and the resource hub."""

from __future__ import annotations

import csv
import json
import textwrap

import pytest

from operonx.app.jobs import (
    CsvSink,
    CsvSource,
    DirSource,
    JsonlSink,
    JsonlSource,
    ListSink,
    NullSink,
    PythonSink,
    PythonSource,
    as_sink,
    as_source,
    open_sink,
    open_source,
)
from operonx.core.registry import ResourceHub


async def _drain(source):
    return [item async for item in source.items()]


# -- sources ----------------------------------------------------------------


async def test_jsonl_source_skips_blank_lines_and_names_a_bad_one(tmp_path):
    p = tmp_path / "in.jsonl"
    p.write_text('{"id": 1}\n\n{"id": 2}\n', encoding="utf-8")
    assert await _drain(JsonlSource(p)) == [{"id": 1}, {"id": 2}]

    p.write_text('{"id": 1}\nnot json\n', encoding="utf-8")
    with pytest.raises(ValueError, match=r"in\.jsonl:2"):
        await _drain(JsonlSource(p))


async def test_csv_source_yields_dicts_of_strings(tmp_path):
    p = tmp_path / "in.csv"
    p.write_text("id,text\na,hello\nb,world\n", encoding="utf-8")
    assert await _drain(CsvSource(p)) == [
        {"id": "a", "text": "hello"},
        {"id": "b", "text": "world"},
    ]


async def test_python_source_takes_every_shape_python_can_iterate(tmp_path, monkeypatch):
    assert await _drain(PythonSource([1, 2])) == [1, 2]
    assert await _drain(PythonSource(iter((3, 4)))) == [3, 4]

    def gen():
        yield 5
        yield 6

    async def agen():
        yield 7

    async def coro():
        return [8, 9]

    assert await _drain(PythonSource(gen)) == [5, 6]
    assert await _drain(PythonSource(agen)) == [7]
    assert await _drain(PythonSource(coro)) == [8, 9]

    (tmp_path / "feeds.py").write_text(
        textwrap.dedent("""
        ITEMS = ["x", "y"]
        def rows():
            yield {"k": 1}
    """),
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    assert await _drain(PythonSource("feeds:ITEMS")) == ["x", "y"]
    assert await _drain(PythonSource("feeds:rows")) == [{"k": 1}]

    with pytest.raises(TypeError, match="not iterable"):
        await _drain(PythonSource(42))


async def test_as_source_picks_by_shape(tmp_path):
    p = tmp_path / "a.jsonl"
    p.write_text("{}\n", encoding="utf-8")
    assert isinstance(as_source(str(p)), JsonlSource)
    assert isinstance(as_source(tmp_path / "b.csv"), CsvSource)
    assert isinstance(as_source([1]), PythonSource)
    assert isinstance(as_source({"not": "a source"}), PythonSource)  # a dict has .items too
    src = JsonlSource(p)
    assert as_source(src) is src
    with pytest.raises(ValueError, match="expected a directory, .jsonl or .csv"):
        as_source(tmp_path / "c.parquet")
    assert isinstance(as_source(tmp_path), DirSource)


def test_open_source_needs_what_each_kind_needs():
    with pytest.raises(ValueError, match="needs `path`"):
        open_source("jsonl")
    with pytest.raises(ValueError, match="needs `entry`"):
        open_source("python")
    with pytest.raises(ValueError, match="unknown source kind"):
        open_source("kafka")


# -- sinks ----------------------------------------------------------------------


async def test_jsonl_sink_puts_the_key_first_and_appends(tmp_path):
    p = tmp_path / "out.jsonl"
    sink = JsonlSink(p)
    await sink.write("k1", {"score": 1, "_key": "ignored"})
    await sink.write("k2", "plain")
    await sink.close()
    rows = [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines()]
    assert rows == [{"_key": "k1", "score": 1}, {"_key": "k2", "item": "plain"}]
    assert list(rows[0]) == ["_key", "score"]

    again = JsonlSink(p)  # append is the default
    await again.write("k3", {})
    await again.close()
    assert len(p.read_text(encoding="utf-8").splitlines()) == 3

    fresh = JsonlSink(p, mode="overwrite", key_field=None)
    await fresh.write("k4", {"only": True})
    await fresh.close()
    assert p.read_text(encoding="utf-8") == '{"only": true}\n'
    with pytest.raises(ValueError, match="mode"):
        JsonlSink(p, mode="truncate")


async def test_csv_sink_writes_one_header_and_keeps_columns_straight(tmp_path):
    p = tmp_path / "out.csv"
    sink = CsvSink(p)
    await sink.write("a", {"score": 1, "note": "x"})
    await sink.write("b", {"score": 2, "extra": "dropped"})
    await sink.close()
    again = CsvSink(p)
    await again.write("c", {"score": 3, "note": "y"})
    await again.close()
    with p.open(encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert rows == [
        {"_key": "a", "score": "1", "note": "x"},
        {"_key": "b", "score": "2", "note": ""},
        {"_key": "c", "score": "3", "note": "y"},
    ]


async def test_list_python_and_null_sinks():
    mine: list = []
    ls = ListSink(mine)
    await ls.write("a", 1)
    assert mine == [1] and ls.pairs == [("a", 1)]

    seen = []
    sync = PythonSink(lambda k, i: seen.append((k, i)))
    await sync.write("b", 2)

    async def awrite(k, i):
        seen.append((k, i, "async"))

    asink = PythonSink(awrite)
    await asink.write("c", 3)
    await asink.close()
    assert seen == [("b", 2), ("c", 3, "async")]

    null = NullSink()
    await null.write("d", 4)
    assert null.written == 1


async def test_as_sink_picks_by_shape(tmp_path):
    assert isinstance(as_sink(None), NullSink)
    target: list = []
    assert as_sink(target).values is target
    assert isinstance(as_sink(str(tmp_path / "o.jsonl")), JsonlSink)
    assert isinstance(as_sink(tmp_path / "o.csv"), CsvSink)
    assert isinstance(as_sink(print), PythonSink)
    sink = ListSink()
    assert as_sink(sink) is sink
    with pytest.raises(TypeError, match="as a sink"):
        as_sink(42)
    with pytest.raises(ValueError, match="unknown sink kind"):
        open_sink("s3")


# -- as resources ---------------------------------------------------------------


@pytest.fixture
def hub(tmp_path):
    (tmp_path / "calls.jsonl").write_text('{"id": "a"}\n', encoding="utf-8")
    (tmp_path / "resources.yaml").write_text(
        textwrap.dedent(f"""
        source:calls:
          kind: jsonl
          path: {tmp_path / "calls.jsonl"}
        source:feed:
          kind: python
          entry: feeds:ITEMS
        sink:scores:
          kind: jsonl
          path: {tmp_path / "scores.jsonl"}
          mode: overwrite
        sink:drop:
          kind: "null"
        llm:not-a-source:
          api_type: openai
          api_key: x
          base_url: http://localhost
          model: m
    """),
        encoding="utf-8",
    )
    hub = ResourceHub.from_yaml(tmp_path / "resources.yaml")
    ResourceHub.set_instance(hub)
    try:
        yield hub
    finally:
        ResourceHub.reset_instance()


async def test_sources_and_sinks_resolve_through_the_hub(hub, tmp_path):
    src = as_source("source:calls")
    assert isinstance(src, JsonlSource) and src.path == tmp_path / "calls.jsonl"
    assert await _drain(src) == [{"id": "a"}]
    assert as_source("source:calls") is src  # cached, like every resource

    feed = as_source("source:feed")
    assert isinstance(feed, PythonSource)

    sink = as_sink("sink:scores")
    assert isinstance(sink, JsonlSink) and sink.mode == "overwrite"
    assert isinstance(as_sink("sink:drop"), NullSink)

    with pytest.raises(KeyError, match="not found"):
        as_source("source:missing")


async def test_a_key_of_the_wrong_category_is_refused(hub):
    import operonx.providers  # noqa: F401 — registers llm:, so the key resolves to a backend

    with pytest.raises((TypeError, KeyError)):
        as_source("llm:not-a-source")
