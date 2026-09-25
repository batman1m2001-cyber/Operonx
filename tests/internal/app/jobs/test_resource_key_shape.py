"""A path is not a resource key, even when it has a colon in it.

`as_source`, `as_sink` and the manifest loader all decided between "resource
key" and "filesystem path" with::

    if ":" in obj and not Path(obj).exists():

Every absolute Windows path contains a colon, and an output file does not
exist before the job writes it — so `Job(sink=r"C:\\jobs\\out.jsonl")` went to
the ResourceHub and failed with `ResourceHub not initialized`, naming a
component the caller had never mentioned. Input paths escaped by accident,
because they happen to exist.

These pin the shape so the next person to touch the heuristic finds out here
rather than on a Windows box.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from operonx.app.jobs._keys import is_resource_key
from operonx.app.jobs.sinks import CsvSink, JsonlSink, NullSink, as_sink
from operonx.app.jobs.sources import JsonlSource, as_source


class TestTheKeyShape:
    @pytest.mark.parametrize(
        "value",
        [
            "source:calls_today",
            "sink:results",
            "trace_local:default",
            "doc_store:corpus-pg",
            "a_b.c-d:name",
        ],
    )
    def test_a_category_and_a_name_is_a_key(self, value):
        assert is_resource_key(value)

    @pytest.mark.parametrize(
        "value",
        [
            r"C:\jobs\out.jsonl",  # the one that broke it
            r"D:\data\in.csv",
            "C:/jobs/out.jsonl",  # same drive, forward slashes
            "/var/data/out.jsonl",
            "out.jsonl",
            "./nested/out.jsonl",
            "./a:b/c.jsonl",  # colon inside a directory name
            "",
        ],
    )
    def test_a_path_is_not_a_key(self, value):
        assert not is_resource_key(value)

    def test_a_single_letter_category_is_not_a_key(self):
        """This is the whole fix: a drive letter is one character.

        Anything shorter than two characters before the colon is a drive, not
        a category. No operonx category is one letter.
        """
        assert not is_resource_key("C:name")
        assert is_resource_key("cc:name")

    @pytest.mark.parametrize("value", [None, 12, Path("out.jsonl"), ["a"]])
    def test_only_strings_can_be_keys(self, value):
        assert not is_resource_key(value)


class TestSinksTakeAbsolutePathsWithoutAHub:
    """No ResourceHub installed — a path must still resolve to a file sink.

    The assertion is the *absence* of `RuntimeError: ResourceHub not
    initialized`. If the colon heuristic returns, this is where it lands.
    """

    def test_an_absolute_path_is_a_file_sink(self, tmp_path):
        sink = as_sink(str(tmp_path / "o.jsonl"))
        assert isinstance(sink, JsonlSink)

    def test_extension_still_picks_the_sink(self, tmp_path):
        assert isinstance(as_sink(str(tmp_path / "o.csv")), CsvSink)
        assert isinstance(as_sink(None), NullSink)

    def test_an_absolute_path_is_a_file_source(self, tmp_path):
        src = tmp_path / "i.jsonl"
        src.write_text('{"a": 1}\n', encoding="utf-8")
        assert isinstance(as_source(str(src)), JsonlSource)

    def test_a_key_still_goes_to_the_hub(self):
        """And fails loudly there, rather than being read as a filename.

        The other half of the fix: `sink:nope` must not silently become a
        file called `sink:nope`. It should reach the hub and be reported as
        the missing resource it is.
        """
        with pytest.raises(Exception) as excinfo:
            as_sink("sink:definitely_not_registered")
        assert "definitely_not_registered" in str(excinfo.value) or "ResourceHub" in str(
            excinfo.value
        )
