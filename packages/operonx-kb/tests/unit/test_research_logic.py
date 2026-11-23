"""The research loop's pure logic (operonx_kb.retrieval.research)."""

from operonx_kb.retrieval.research import add_usage, merge_hits, research_advance, research_request


def hit(c, text="t", key="k"):
    return {"chunk_id": c, "text": text, "key": key}


def test_merge_keeps_numbers_stable_and_drops_repeats():
    merged = merge_hits([hit("a"), hit("b")], [hit("b"), hit("c")])
    assert [h["chunk_id"] for h in merged] == ["a", "b", "c"]
    assert merge_hits(None, [hit("x")]) == [hit("x")]


def test_the_request_numbers_passages_and_counts_searches_left():
    msgs = research_request("q?", [hit("a", "x " * 600, "doc.md")], ["q?"], 2)
    user = msgs[1]["content"]
    assert "1. q?" in user and "2 more searches allowed." in user and "[1] doc.md" in user
    assert not user.endswith("…")  # a long passage is seen whole: its fact may sit at the end
    huge = research_request("q?", [hit("a", "x " * 5000, "doc.md")], ["q?"], 2)[1]["content"]
    assert huge.endswith("…")  # only an outsized one is cut
    assert "No search is left." in research_request("q", [], ["q"], 0)[1]["content"]


def test_advance_stops_on_empty_repeated_or_spent():
    hits = [hit("a"), hit("b")]
    assert research_advance("", [2], hits, ["q"], 4)["done"]
    assert research_advance(" Q ", [2], hits, ["q"], 4)["stats"]["repeated"]
    assert research_advance("new", [2], hits, ["q", "r"], 2)["done"]
    go = research_advance("new  search", [2], hits, ["q"], 4)
    assert go["next"] == "new search" and not go["done"]
    assert [h["chunk_id"] for h in go["kept"]] == ["b"]


def test_keep_tolerates_strings_and_bad_numbers():
    hits = [hit("a"), hit("b"), hit("c")]
    assert [h["chunk_id"] for h in research_advance("", "3, 1", hits, [], 2)["kept"]] == ["c", "a"]
    assert [
        h["chunk_id"] for h in research_advance("", [0, 9, "x", 2, 2], hits, [], 2)["kept"]
    ] == ["b"]
    assert len(research_advance("", None, hits, [], 2)["kept"]) == 3


def test_usage_adds_numeric_fields():
    total = add_usage(
        {"prompt_tokens": 3, "model": "m"}, {"prompt_tokens": 2, "cost_usd": 0.5, "ok": True}
    )
    assert total == {"prompt_tokens": 5, "model": "m", "cost_usd": 0.5}
