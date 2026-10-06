"""The concept graph through the real ingest and search graphs (PLAN G2-G5).

A two-hop question in miniature: the film's paragraph names its director, and
the director's paragraph — which shares no word with the question — is titled
with that name. Hybrid finds the film; the graph walk finds the director.
"""

import asyncio

import pytest

from operonx_kb import QueryError

FILM = "# Polish War\n\nPolish War is a 2009 picture made by Xawery Zulawski in Warsaw.\n"
PERSON = (
    "# Xawery Zulawski\n\nXawery Zulawski was born in 1971. Malgorzata Braunek is his mother.\n"
)
MOTHER = "# Malgorzata Braunek\n\nMalgorzata Braunek was an actress of stage and screen.\n"
OTHERS = {
    f"other{i}.md": f"# Topic {i}\n\nA note about gardens, rivers and number {i} of the series.\n"
    for i in range(6)
}
QUESTION = "who made the 2009 picture Polish War"


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def wiki(hub, tmp_path):
    from operonx_kb import ChunkerSpec, CollectionSpec, DenseIndexSpec, GraphSpec, KnowledgeBase
    from operonx_kb.model.collection import LexicalIndexSpec
    from operonx_kb.testing import RecordingConsumer

    hub.alias("embedding:hash", "fake_embedding:hash")
    kb = KnowledgeBase(trace=RecordingConsumer())
    kb.create_collection(
        "wiki",
        CollectionSpec(
            chunker=ChunkerSpec(max_tokens=120, min_tokens=16),
            dense=DenseIndexSpec(embedder="hash", store="vector_store:kb"),
            lexical=LexicalIndexSpec(),
            filterable={"dept": "keyword"},
            graph=GraphSpec(seeds=1, max_df_min=3),  # one seed: the hops are what is tested
        ),
    )
    docs = {"film.md": FILM, "person.md": PERSON, "mother.md": MOTHER, **OTHERS}
    for key, text in docs.items():
        (tmp_path / key).write_text(text, encoding="utf-8")
        dept = "hidden" if key == "person.md" else "open"
        run(kb.add("wiki", str(tmp_path / key), key=key, metadata={"dept": dept}))
    kb.tmp = tmp_path
    return kb


def keys(out):
    return [h["key"] for h in out["hits"]]


def test_the_walk_brings_the_linked_paragraph_after_its_seed(wiki):
    out = run(wiki.search("wiki", QUESTION, mode="graph", k=4))
    assert keys(out)[:3] == ["film.md", "person.md", "mother.md"]
    top = out["hits"][1]
    assert top["retriever"] == "graph" and top["scores"]["graph"] > 0
    assert out["hits"][1]["text"].startswith("Xawery Zulawski was born")


def test_ingest_commits_each_chunks_concepts_with_the_version(wiki):
    mentions = wiki.catalog.graph_mentions("wiki")
    concepts = {(wiki.catalog.get_document(d).key, c) for _, d, c, _ in mentions}
    assert ("film.md", "xawery zulawski") in concepts and (
        "person.md",
        "xawery zulawski",
    ) in concepts
    assert ("film.md", "polish war") in concepts  # the title


def test_a_filter_keeps_the_walk_out_of_the_documents_it_hides(wiki):
    out = run(wiki.search("wiki", QUESTION, mode="graph", k=4, filter={"fields": {"dept": "open"}}))
    assert "person.md" not in keys(out)
    assert "mother.md" not in keys(out)[:2]  # reachable only through the hidden one


def test_an_edit_a_delete_and_a_re_add_change_the_graph_at_once(wiki):
    (wiki.tmp / "film.md").write_text(FILM.replace("Xawery Zulawski", "an unknown crew"), "utf-8")
    run(wiki.add("wiki", str(wiki.tmp / "film.md"), key="film.md", metadata={"dept": "open"}))
    assert "person.md" not in keys(run(wiki.search("wiki", QUESTION, mode="graph", k=3)))[:2]
    (wiki.tmp / "film.md").write_text(FILM, "utf-8")
    run(wiki.add("wiki", str(wiki.tmp / "film.md"), key="film.md", metadata={"dept": "open"}))
    assert keys(run(wiki.search("wiki", QUESTION, mode="graph", k=3)))[:2] == [
        "film.md",
        "person.md",
    ]
    run(wiki.delete("wiki", "person.md"))
    assert "person.md" not in keys(run(wiki.search("wiki", QUESTION, mode="graph", k=5)))


def test_an_unchanged_re_add_skips_and_a_purge_leaves_no_mention(wiki):
    before = wiki.catalog.graph_mentions("wiki")
    got = run(wiki.add("wiki", str(wiki.tmp / "film.md"), key="film.md", metadata={"dept": "open"}))
    assert got["action"] == "skip" and wiki.catalog.graph_mentions("wiki") == before
    run(wiki.delete("wiki", "person.md"))
    run(wiki.gc("wiki"))
    with wiki.catalog._tx() as c:
        left = c.rows("SELECT COUNT(*) AS n FROM kb_graph_mentions m JOIN kb_documents d "
                      "ON d.active_version_id = m.version_id WHERE d.key = 'person.md'")  # fmt: skip
    assert left[0]["n"] == 0


def test_graph_mode_needs_the_graph_spec(kbx):
    with pytest.raises(QueryError, match="no concept graph"):
        run(kbx.search("docs", "anything", mode="graph"))


def test_expand_caps_what_the_walk_brings_and_needs_no_re_ingest(wiki):
    from operonx_kb import GraphSpec

    spec = wiki.collection("wiki").spec
    wiki.create_collection(
        "wiki", spec.model_copy(update={"graph": GraphSpec(seeds=1, expand=1, max_df_min=3)})
    )
    out = run(wiki.search("wiki", QUESTION, mode="graph", k=4))
    hybrid = keys(run(wiki.search("wiki", QUESTION, mode="hybrid", k=10)))
    # one chunk brought (the director's), then hybrid's own order
    assert keys(out) == [
        "film.md",
        "person.md",
        *[k for k in hybrid if k not in ("film.md", "person.md")][:2],
    ]
    got = run(wiki.add("wiki", str(wiki.tmp / "film.md"), key="film.md", metadata={"dept": "open"}))
    assert got["action"] == "skip"  # query-time settings are not in the pipeline fingerprint
