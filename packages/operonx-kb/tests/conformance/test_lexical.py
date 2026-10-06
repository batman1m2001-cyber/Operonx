"""The LexicalIndex contract, run against every lexical backend.

SQLite FTS5 always; Postgres FTS when ``KB_TEST_PG_DSN`` names a database
(each test gets a throwaway schema, dropped afterwards).
"""

import os
import uuid

import pytest

from operonx_kb.model.collection import AnalyzerSpec
from operonx_kb.stores.lexical.sqlite import SqliteLexicalIndex
from operonx_kb.text.analyze import Analyzer

PG_DSN = os.environ.get("KB_TEST_PG_DSN")
BACKENDS = [
    "sqlite",
    pytest.param("postgres", marks=pytest.mark.skipif(not PG_DSN, reason="set KB_TEST_PG_DSN")),
]
VI = Analyzer(AnalyzerSpec(kind="vi"))


def payload(doc="doc_a", collection="docs"):
    return {
        "kb_collection": collection,
        "kb_document": doc,
        "kb_tags": [],
        "kb_acl": [],
        "kb_mime": "text/plain",
        "kb_created": 0.0,
    }


@pytest.fixture(params=BACKENDS)
def index(request, tmp_path):
    if request.param == "sqlite":
        yield SqliteLexicalIndex(tmp_path / "lex.db")
        return
    from operonx_kb.stores.lexical.postgres import PostgresLexicalIndex

    idx = PostgresLexicalIndex(PG_DSN, schema=f"kblex_{uuid.uuid4().hex[:10]}")
    yield idx
    idx.drop_schema()
    idx.close()


def put(index, rows, collection=None):
    ids = [k for k, _ in rows]
    return index.upsert(ids, [VI.tokens(t) for _, t in rows], [payload()] * len(rows), collection)


def test_search_ranks_by_matching_tokens(index):
    put(index, [(1, "annual leave is twelve days"), (2, "sick leave"), (3, "parking rules")])
    ids, scores = index.search(VI.query_tokens("annual leave days"), top_k=10)
    assert ids[:2] == [1, 2] and 3 not in ids
    assert scores == sorted(scores, reverse=True)


def test_tokens_are_kept_as_given_diacritics_and_bigrams(index):
    put(index, [(1, "Nhân viên được nghỉ phép năm"), (2, "nghi phep")])
    assert index.search(["nghỉ_phép"], top_k=5)[0] == [1]
    assert index.search(["phép"], top_k=5)[0] == [1]  # the accented token only
    assert index.search(["phep"], top_k=5)[0] == [2]


def test_upsert_replaces_and_delete_removes(index):
    assert put(index, [(7, "old words here")]) == 1
    put(index, [(7, "new text")])
    assert index.search(["old"], top_k=5)[0] == []
    assert index.search(["new"], top_k=5)[0] == [7]
    assert index.ids() == {7}
    assert index.delete([7, 99]) == 1
    assert index.ids() == set() and index.search(["new"], top_k=5)[0] == []


def test_collections_are_separate_tables(index):
    put(index, [(1, "alpha")], collection="a")
    put(index, [(2, "alpha")], collection="b")
    assert index.search(["alpha"], top_k=5, collection="a")[0] == [1]
    index.drop("a")
    assert index.ids("a") == set() and index.ids("b") == {2}


def test_empty_batches_queries_and_missing_tables_are_quiet(index):
    assert index.upsert([], [], []) == 0
    assert index.delete([]) == 0
    assert index.search([], top_k=5) == ([], [])
    assert index.search(["x"], top_k=5, collection="never") == ([], [])
    assert index.delete([1], collection="never") == 0


def test_a_payload_key_outside_the_kb_payload_is_refused(index):
    with pytest.raises(ValueError, match="tenant"):
        index.upsert([1], [["a"]], [{**payload(), "tenant": "x"}])


def test_a_filter_of_the_wrong_shape_is_refused(index):
    put(index, [(1, "alpha")])
    with pytest.raises(ValueError, match="KBFilter"):
        index.search(["alpha"], top_k=5, filter={"tenant": "acme"})
