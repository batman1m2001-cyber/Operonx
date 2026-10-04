"""Reciprocal rank fusion."""

import random

from hypothesis import given
from hypothesis import strategies as st

from operonx_kb.retrieval.fusion import rrf_fuse


def hits(ids, retriever):
    return [{"chunk_id": c, "score": 1.0 / (i + 1), "rank": i + 1, "retriever": retriever,
             "scores": {retriever: 1.0 / (i + 1)}} for i, c in enumerate(ids)]  # fmt: skip


def test_a_chunk_both_lists_rank_well_wins():
    fused = rrf_fuse([hits(["a", "b", "c"], "dense"), hits(["b", "d", "a"], "lexical")])
    assert [h["chunk_id"] for h in fused] == ["b", "a", "d", "c"]
    assert fused[0]["score"] == 1 / 62 + 1 / 61
    assert fused[0]["scores"] == {"dense": 0.5, "lexical": 1.0}
    assert {h["retriever"] for h in fused} == {"hybrid"} and [h["rank"] for h in fused] == [
        1,
        2,
        3,
        4,
    ]


def test_weights_and_empty_lists():
    assert rrf_fuse([[], []]) == []
    only = rrf_fuse([hits(["a"], "dense"), []])
    assert [h["chunk_id"] for h in only] == ["a"]
    tilted = rrf_fuse([hits(["a", "b"], "dense"), hits(["b", "a"], "lexical")], weights=[2, 1])
    assert tilted[0]["chunk_id"] == "a"


@given(
    st.lists(st.lists(st.sampled_from("abcdefgh"), unique=True, max_size=8), min_size=1, max_size=4)
)
def test_rrf_does_not_depend_on_the_order_of_the_lists(ids_lists):
    lists = [hits(ids, f"r{i}") for i, ids in enumerate(ids_lists)]
    shuffled = lists[:]
    random.Random(7).shuffle(shuffled)
    a = [(h["chunk_id"], round(h["score"], 12)) for h in rrf_fuse(lists)]
    b = [(h["chunk_id"], round(h["score"], 12)) for h in rrf_fuse(shuffled)]
    assert a == b
