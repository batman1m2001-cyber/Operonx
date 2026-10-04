"""K4 gates on the real graphs: contextual enrichment, the tree index and tree search
(PLAN §9), with a scripted model, the counting embedder, FAISS and the trace.

Model calls are counted twice: the scripted model's own record, and the ``LLMOp``
spans of the run traces (``call`` inside an enrichment stage, ``nav`` in tree search).
"""

import asyncio
import json
import re

import pytest

from operonx_kb import ChunkerSpec, CollectionSpec, ContextualSpec, DenseIndexSpec, TreeSpec
from operonx_kb.enrich.contextual import CONTEXT_SYSTEM
from operonx_kb.enrich.tree import NAVIGATOR_SYSTEM, SUMMARY_SYSTEM, TOC_SYSTEM
from operonx_kb.kb import IngestError
from operonx_kb.model.collection import LexicalIndexSpec


def run(coro):
    return asyncio.run(coro)


class Model:
    """Answers each enrichment prompt from what the prompt holds, deterministically.

    ``fail`` is a set of substrings: a request whose user message contains one
    raises (a provider error), so the stage loses that answer.
    """

    def __init__(self):
        self.fail = set()
        self.calls = {"contextual": 0, "summary": 0, "toc": 0, "navigator": 0}
        self.navigate = None
        self.toc_reply = None

    def __call__(self, messages):
        system, user = messages[0]["content"], messages[-1]["content"]
        if any(f in user for f in self.fail):
            raise ConnectionRefusedError("the provider is down")
        if system == CONTEXT_SYSTEM:
            self.calls["contextual"] += 1
            section = re.search(r"^Section: (.*)$", user, re.M).group(1)
            return f"This chunk belongs to {section}."
        if system == SUMMARY_SYSTEM:
            self.calls["summary"] += 1
            part = re.search(r"^Part: (.*)$", user, re.M).group(1)
            return f"About {part}."
        if system == TOC_SYSTEM:
            self.calls["toc"] += 1
            if self.toc_reply is not None:
                return self.toc_reply
            first, last = map(int, re.search(r"Blocks (\d+) to (\d+):", user).groups())
            sections = [{"title": f"Part {b}", "first_block": b, "level": 1}
                        for b in range(first, last + 1, 3)]  # fmt: skip
            sections.append({"title": "Out of range", "first_block": last + 50})
            return json.dumps({"sections": sections})
        if system.startswith(NAVIGATOR_SYSTEM.split("\n", 1)[0]):
            self.calls["navigator"] += 1
            return json.dumps(self.navigate(user))
        raise AssertionError(f"unexpected prompt: {system[:60]}")


@pytest.fixture
def model(hub):
    hub.alias("llm:scripted", "fake_llm:scripted")
    m = Model()
    hub.get("fake_llm:scripted").script = m
    return m


def _kb(hub, spec):
    from operonx_kb import KnowledgeBase
    from operonx_kb.testing import RecordingConsumer

    hub.alias("embedding:hash", "fake_embedding:hash")
    recorder = RecordingConsumer()
    kb = KnowledgeBase(trace=recorder)
    kb.recorder = recorder
    kb.create_collection("docs", spec)
    kb.embedder = hub.get("fake_embedding:hash")
    return kb


def _spec(chunk_tokens=60, **enrich):
    return CollectionSpec(
        chunker=ChunkerSpec(max_tokens=chunk_tokens, min_tokens=8),
        dense=DenseIndexSpec(embedder="hash", store="vector_store:kb"),
        lexical=LexicalIndexSpec(),
        **enrich,
    )


def _manual(n, edited=-1, paragraphs=1):
    parts = ["# Policy manual\n"]
    for i in range(n):
        parts.append(f"## Topic {i}\n")
        for j in range(paragraphs):
            text = f"Section {i} paragraph {j} explains rule number {i} in enough words to stand alone."
            if i == edited and j == 0:
                text = text.replace("rule number", "the revised rule number")
            parts.append(text + "\n")
    return "\n".join(parts)


def _embed_texts(kb):
    chunks = kb.catalog.get_chunks(sorted(kb.catalog.active_chunk_ids(collection_id="docs")))
    return {c.text: c.embed_text for c in chunks.values()}


# -- contextual ---------------------------------------------------------------------------


def test_contextual_ingest_prepends_contexts_and_reingest_makes_no_model_call(hub, model, tmp_path):
    kb = _kb(hub, _spec(contextual=ContextualSpec(llm="scripted")))
    path = tmp_path / "manual.md"
    path.write_text(_manual(6), encoding="utf-8")
    first = run(kb.add("docs", str(path)))
    chunks = first["stats"]["chunking"]["chunks"]
    stats = first["stats"]["contextual"]
    assert (
        stats["calls"] == model.calls["contextual"] == chunks and stats["contextualized"] == chunks
    )
    assert kb.recorder.runs("call") == chunks
    for text, embedded in _embed_texts(kb).items():
        # The context leads what is embedded and indexed; the chunk's own text is untouched.
        topic = re.search(r"Section (\d+)", text).group(1)
        assert embedded.startswith(f"This chunk belongs to Policy manual > Topic {topic}.")
        assert embedded.endswith(text)
    assert kb.verify("docs").ok
    hit = run(kb.search("docs", "rule number 3", mode="lexical", k=1))["hits"][0]
    assert "Section 3" in hit["text"] and "belongs to" not in hit["text"]

    calls, spans = dict(model.calls), kb.recorder.runs("call")
    again = run(kb.add("docs", str(path)))
    assert again["action"] == "skip"
    assert (
        model.calls == calls and kb.recorder.runs("call") == spans
    )  # 0 model calls, 0 LLMOp spans

    run(kb.delete("docs", str(path), purge=True))
    texts = len(kb.embedder.texts)
    back = run(kb.add("docs", str(path)))
    assert back["action"] == "new" and back["stats"]["contextual"]["cached"] == chunks
    assert model.calls == calls and kb.recorder.runs("call") == spans  # answered by the cache
    assert len(kb.embedder.texts) == texts  # the embedding cache too
    assert kb.verify("docs").ok


def test_an_edit_recontextualizes_only_its_window(hub, model, tmp_path):
    spec = _spec(chunk_tokens=20, contextual=ContextualSpec(llm="scripted", window_tokens=400))
    kb = _kb(hub, spec)
    path = tmp_path / "manual.md"
    path.write_text(_manual(30, paragraphs=3), encoding="utf-8")
    first = run(kb.add("docs", str(path)))
    total = first["stats"]["chunking"]["chunks"]
    assert total == 90 and model.calls["contextual"] == 90
    path.write_text(_manual(30, edited=17, paragraphs=3), encoding="utf-8")
    second = run(kb.add("docs", str(path)))
    # Topic 17's three chunks share one window: the edited one is new, and the other
    # two see a changed window, so they are new chunks too. Nothing else moves.
    assert second["stats"]["chunking"] == {"chunks": 90, "new": 3, "reused": 87, "removed": 3}
    assert model.calls["contextual"] == 93 and second["stats"]["contextual"]["calls"] == 3
    assert second["stats"]["gc_deleted"] == 3
    assert kb.verify("docs").ok


def test_turning_contextual_on_reindexes_every_chunk(hub, model, tmp_path):
    kb = _kb(hub, _spec())
    path = tmp_path / "manual.md"
    path.write_text(_manual(5), encoding="utf-8")
    plain = run(kb.add("docs", str(path)))
    assert model.calls["contextual"] == 0 and plain["stats"]["contextual"] == {}
    old = kb.catalog.active_chunk_ids(collection_id="docs")
    kb.create_collection("docs", _spec(contextual=ContextualSpec(llm="scripted")))
    again = run(kb.add("docs", str(path)))
    assert again["action"] == "update"  # the pipeline fingerprint includes the enricher
    n = again["stats"]["chunking"]["chunks"]
    assert again["stats"]["chunking"] == {"chunks": n, "new": n, "reused": 0, "removed": n}
    assert kb.catalog.active_chunk_ids(collection_id="docs").isdisjoint(old)
    assert all(e.startswith("This chunk belongs to") for e in _embed_texts(kb).values())
    assert kb.verify("docs").ok


def test_a_failed_model_call_fails_the_ingest_and_the_retry_pays_only_for_it(hub, model, tmp_path):
    kb = _kb(hub, _spec(contextual=ContextualSpec(llm="scripted")))
    path = tmp_path / "manual.md"
    path.write_text(_manual(6), encoding="utf-8")
    model.fail = {"Section 4 paragraph 0"}
    with pytest.raises(IngestError, match="the provider is down") as failed:
        run(kb.add("docs", str(path)))
    assert "answers.done" in str(failed.value)  # the stage refused to go on without it
    assert kb.documents("docs") == []  # not committed half enriched
    asked = model.calls["contextual"]
    model.fail = set()
    done = run(kb.add("docs", str(path)))
    assert done["stats"]["contextual"]["calls"] == 1  # only the one that failed
    assert done["stats"]["contextual"]["cached"] == done["stats"]["chunking"]["chunks"] - 1
    assert model.calls["contextual"] == asked + 1
    assert kb.verify("docs").ok


def test_an_unparsable_table_of_contents_fails_the_ingest(hub, model, tmp_path):
    kb = _kb(hub, _spec(tree=TreeSpec(llm="scripted", toc_min_tokens=50)))
    model.toc_reply = "Sections: intro, body, end."  # no JSON object
    path = tmp_path / "notes.txt"
    path.write_text(
        "\n\n".join(f"Paragraph {i} is long enough to count as a block of text." for i in range(7))
    )
    with pytest.raises(IngestError) as failed:
        run(kb.add("docs", str(path)))
    assert "toc requests got no answer" in str(failed.value) or "ParserError" in str(failed.value)
    assert "toc.kept" in str(failed.value)  # keep_answer refused the unparsed answer
    assert kb.documents("docs") == []


# -- the tree index ---------------------------------------------------------------------------


def test_tree_from_headings_with_summaries_and_free_reingest(hub, model, tmp_path):
    kb = _kb(hub, _spec(tree=TreeSpec(llm="scripted", summary_input_tokens=100)))
    path = tmp_path / "manual.md"
    path.write_text(_manual(8), encoding="utf-8")
    first = run(kb.add("docs", str(path)))
    version = kb.document("docs", str(path)).active_version_id
    nodes = kb.catalog.tree_nodes(version)
    canonical = kb.canonical_text(version)
    assert [n.title for n in nodes] == ["Policy manual"] + [f"Topic {i}" for i in range(8)]
    assert [n.source for n in nodes] == ["document"] + ["heading"] * 8
    assert all(n.parent_id == nodes[0].id for n in nodes[1:])
    assert all(canonical[n.span[0] : n.span[1]].startswith(n.title) for n in nodes[1:])
    assert (
        nodes[3].summary == "About Topic 2." and nodes[0].summary == "About (the whole document)."
    )
    assert model.calls == {"contextual": 0, "summary": 9, "toc": 0, "navigator": 0}
    assert first["stats"]["tree"]["summary_usage"]["calls"] == 9
    assert first["stats"]["commit"]["tree_nodes"] == 9

    spans = kb.recorder.runs("call")
    assert run(kb.add("docs", str(path)))["action"] == "skip"
    assert kb.recorder.runs("call") == spans and model.calls["summary"] == 9

    # An edit re-summarizes the edited section only: the root is summarized from its
    # outline (its text is over summary_input_tokens), which the edit leaves alone.
    path.write_text(_manual(8, edited=5), encoding="utf-8")
    run(kb.add("docs", str(path)))
    assert model.calls["summary"] == 10
    assert kb.verify("docs").ok


def test_a_document_without_headings_gets_a_synthesized_table_of_contents(hub, model, tmp_path):
    kb = _kb(hub, _spec(tree=TreeSpec(llm="scripted", toc_min_tokens=50)))
    path = tmp_path / "notes.txt"
    paragraphs = [
        f"Paragraph {i} talks about subject {i} at some length to be a block." for i in range(7)
    ]
    path.write_text("\n\n".join(paragraphs), encoding="utf-8")
    first = run(kb.add("docs", str(path)))
    version = kb.document("docs", str(path)).active_version_id
    nodes = kb.catalog.tree_nodes(version)
    canonical = kb.canonical_text(version)
    assert [n.title for n in nodes[1:]] == ["Part 0", "Part 3", "Part 6"]
    assert {n.source for n in nodes[1:]} == {"toc"}
    assert canonical[nodes[1].span[0] :].startswith("Paragraph 0")
    assert canonical[nodes[2].span[0] :].startswith("Paragraph 3")
    assert nodes[1].span[1] == nodes[2].span[0] and nodes[3].span[1] == len(canonical)
    assert first["stats"]["tree"]["toc_dropped"] == 1  # the entry naming no block of its window
    assert model.calls["toc"] == 1 and model.calls["summary"] == 4
    run(kb.delete("docs", str(path), purge=True))
    run(kb.add("docs", str(path)))
    assert model.calls["toc"] == 1 and model.calls["summary"] == 4  # all from the cache


def test_short_documents_without_headings_are_one_node(hub, model, tmp_path):
    kb = _kb(hub, _spec(tree=TreeSpec(llm="scripted")))
    path = tmp_path / "short.txt"
    path.write_text("One short paragraph.\n\nAnother one.", encoding="utf-8")
    run(kb.add("docs", str(path)))
    nodes = kb.catalog.tree_nodes(kb.document("docs", str(path)).active_version_id)
    assert [(n.source, n.depth) for n in nodes] == [("document", 0)]
    assert model.calls["toc"] == 0 and model.calls["summary"] == 1


# -- tree search ---------------------------------------------------------------------------------


def _options(user):
    return re.findall(r"^\[(\d+)\] (.*?) — (.*?)( \(leaf\))?$", user, re.M)


def test_tree_search_walks_the_picked_documents_and_backfills_with_the_seed(hub, model, tmp_path):
    kb = _kb(hub, _spec(tree=TreeSpec(llm="scripted", docs=2, beam=1)))
    for name, topics in (("a.md", "apples"), ("b.md", "pears")):
        body = [f"# Guide to {topics}\n"]
        for i in range(3):
            body.append(f"## Chapter {i}\n\n### Part {i}.0\n\nAbout {topics} {i}, first part.\n")
            body.append(f"### Part {i}.1\n\nAbout {topics} {i}, second part with the tariff.\n")
        (tmp_path / name).write_text("\n".join(body), encoding="utf-8")
        run(kb.add("docs", str(tmp_path / name)))
    seen = []

    def navigate(user):
        options = _options(user)
        seen.append([(o[1], o[2], bool(o[3])) for o in options])
        wanted = "Chapter 1" if len(seen) == 1 else "Part 1.1"
        n = next(o[0] for o in options if o[2].endswith(wanted) and "pears" in o[1])
        return {"choose": [int(n), 999], "enough": False}

    model.navigate = navigate
    out = run(kb.search("docs", "pears tariff", mode="tree", k=4))
    assert model.calls["navigator"] == 2 and kb.recorder.runs("nav") == 2
    # Step 1 shows the chapters of both candidate documents; step 2 the parts of the pick.
    assert {o[1] for o in seen[0]} == {"Chapter 0", "Chapter 1", "Chapter 2"} and len(seen[0]) == 6
    assert [o[1] for o in seen[1]] == ["Chapter 1 > Part 1.0", "Chapter 1 > Part 1.1"]
    assert all(o[2] for o in seen[1])  # leaves
    hits = out["hits"]
    assert hits[0]["text"] == "About pears 1, second part with the tariff."
    assert [h["retriever"] for h in hits] == ["tree"] * 4 and len(
        {h["chunk_id"] for h in hits}
    ) == 4
    assert all("$errors" not in str(h) for h in hits)


def test_tree_search_without_any_tree_returns_the_seed(hub, model, tmp_path):
    kb = _kb(hub, _spec())
    path = tmp_path / "manual.md"
    path.write_text(_manual(4), encoding="utf-8")
    run(kb.add("docs", str(path)))
    seed = run(kb.search("docs", "rule number 2", k=3))["hits"]
    kb.create_collection("docs", _spec(tree=TreeSpec(llm="scripted")))  # documents not re-added
    out = run(kb.search("docs", "rule number 2", mode="tree", k=3))
    assert model.calls["navigator"] == 0
    assert [h["chunk_id"] for h in out["hits"]] == [h["chunk_id"] for h in seed]


def test_tree_mode_needs_a_tree_spec(hub, model):
    from operonx_kb import QueryError

    kb = _kb(hub, _spec())
    with pytest.raises(QueryError, match="no tree index"):
        kb.retriever("docs", "tree")
