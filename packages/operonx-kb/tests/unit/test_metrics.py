"""Retrieval and answer metrics over quote-anchored labels."""

import math

import pytest

from operonx_kb.eval.labels import occurrences
from operonx_kb.eval.metrics import (
    citation_precision,
    covers,
    faithfulness_proxy,
    grounded_recall,
    ndcg_at,
    recall_at,
    reciprocal_rank,
)

A = [("v1", (100, 140))]  # one label, one occurrence
B = [("v1", (300, 320)), ("v2", (10, 30))]  # one label, two occurrences


def hit(version, *spans):
    return {"version_id": version, "spans": [list(s) for s in spans]}


def test_a_hit_covers_a_label_holding_half_of_an_occurrence():
    assert covers(hit("v1", (90, 200)), A)
    assert covers(hit("v1", (120, 200)), A)  # exactly half
    assert not covers(hit("v1", (121, 200)), A)
    assert not covers(hit("v2", (90, 200)), A)  # another version
    assert covers(hit("v2", (0, 50)), B) and covers(hit("v1", (0, 5), (300, 330)), B)


def test_recall_mrr_ndcg():
    miss, a, b = hit("v1", (0, 50)), hit("v1", (100, 140)), hit("v2", (0, 40))
    hits = [miss, a, a, b]
    assert recall_at(hits, [A, B], 1) == 0 and recall_at(hits, [A, B], 2) == 0.5
    assert recall_at(hits, [A, B], 4) == 1.0
    assert reciprocal_rank(hits, [A, B]) == 0.5 and reciprocal_rank([miss], [A]) == 0.0
    # gains at ranks 2 and 4 (rank 3 covers A again: no gain)
    dcg = 1 / math.log2(3) + 1 / math.log2(5)
    assert ndcg_at(hits, [A, B], 10) == pytest.approx(dcg / (1 + 1 / math.log2(3)))
    assert ndcg_at([a], [A], 10) == 1.0
    with pytest.raises(ValueError):
        recall_at(hits, [], 5)


def test_occurrences_are_whitespace_insensitive_and_all_found():
    text = "Twelve days of\nleave. Later: twelve  days of leave."
    assert occurrences(text, "twelve days of leave") == [(29, 50)]
    assert len(occurrences(text.lower(), "twelve days of leave")) == 2
    assert occurrences(text, "   ") == []


ANSWER = {
    "text": "Staff get twelve days [1]. Unused days expire in March [2]. Ask HR.",
    "citations": [
        {
            "source": 1,
            "quote": "twelve days of annual leave",
            "version_id": "v1",
            "span": [100, 127],
        },
        {
            "source": 2,
            "quote": "Unused days expire on 31 March",
            "version_id": "v1",
            "span": [300, 330],
        },
    ],
    "dropped": [{"citation": {"source": 3, "quote": "x"}, "reason": "there is no source [3]"}],
}


def test_citation_precision():
    assert citation_precision(ANSWER) == pytest.approx(2 / 3)
    assert citation_precision({"citations": [], "dropped": []}) is None


def test_faithfulness_proxy_rewards_words_found_in_the_cited_quotes():
    # s1: {staff, get, twelve, days} -> twelve, days = 0.5; s2: {unused, days, expire, in,
    # march} -> 4/5; s3 cites nothing -> 0
    assert faithfulness_proxy(ANSWER) == pytest.approx((0.5 + 0.8 + 0) / 3)
    assert faithfulness_proxy({"text": ""}) == 0.0


def test_grounded_recall():
    assert grounded_recall(ANSWER, [A, [("v1", (305, 320))]]) == 1.0
    assert grounded_recall(ANSWER, [[("v9", (0, 10))]]) == 0.0


def test_a_quote_with_characters_the_canonical_text_drops_still_resolves():
    """XQuAD-vi contexts hold zero-width spaces; the canonical serializer drops them, so a
    label quoting the raw text must be cleaned the same way (2 of 397 xquad_vi labels)."""
    from operonx_kb.text.normalize import normalize_inline

    raw = "Tòa án Hiến pháp Ý có ý kiến ​​rằng vì luật quốc hữu hóa"
    canonical = normalize_inline(raw)
    assert "​" not in canonical
    assert occurrences(canonical, raw) == [(0, len(canonical))]
