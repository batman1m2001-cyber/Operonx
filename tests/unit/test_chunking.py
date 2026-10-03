from operonx_kb.chunking import (
    RecursiveChunker,
    StructuralChunker,
    chunker_from_spec,
    heading_paths,
    materialize,
)
from operonx_kb.model.collection import ChunkerSpec
from operonx_kb.parsing.base import ParsedDoc, RawBlock
from operonx_kb.structure.build import build_version
from operonx_kb.text.spans import check_chunks

SENT = "The quick brown fox jumps over the lazy dog near the river bank today."


def _tree(*blocks):
    return build_version(ParsedDoc(blocks=list(blocks)), "ver_c")


def _chunks(tree, chunker):
    chunks, occ = materialize(
        tree, chunker.draft(tree), document_id="doc_c", version_id="ver_c", chunker=chunker
    )
    check_chunks(tree.canonical, occ, {c.id: c.content_sha for c in chunks})
    return chunks, occ


def test_peers_pack_under_one_heading_but_never_across_headings():
    tree = _tree(
        RawBlock(kind="title", text="Doc"),
        RawBlock(kind="heading", level=1, text="A"),
        RawBlock(kind="paragraph", text="one."),
        RawBlock(kind="list_item", text="two"),
        RawBlock(kind="list_item", text="three"),
        RawBlock(kind="heading", level=1, text="B"),
        RawBlock(kind="paragraph", text="four."),
    )
    chunks, occ = _chunks(tree, StructuralChunker(max_tokens=100, min_tokens=0))
    assert [c.text for c in chunks] == ["one.\n\n- two\n- three", "four."]
    assert [c.heading_path for c in chunks] == [["Doc", "A"], ["Doc", "B"]]
    assert chunks[0].embed_text == "Doc > A\n\none.\n\n- two\n- three"
    assert occ[0].spans == [(tree.canonical.index("one."), tree.canonical.index("three") + 5)]


def test_oversize_paragraph_splits_at_sentences_then_words():
    long = " ".join([SENT] * 6)
    tree = _tree(
        RawBlock(kind="paragraph", text=long), RawBlock(kind="paragraph", text="word " * 80)
    )
    chunker = StructuralChunker(max_tokens=40, min_tokens=0)
    chunks, _ = _chunks(tree, chunker)
    assert all(c.token_count <= 40 for c in chunks)
    assert all(c.text.endswith(".") for c in chunks if c.text.startswith("The quick"))
    assert len(chunks) >= 4


def test_table_is_an_evidence_unit_with_caption_and_referring_paragraph():
    tree = _tree(
        RawBlock(kind="heading", level=1, text="Leave"),
        RawBlock(kind="paragraph", text="Table 2 lists the days."),
        RawBlock(kind="paragraph", text="Unrelated text."),
        RawBlock(kind="caption", text="Table 2: Days"),
        RawBlock(kind="table", attrs={"rows": [["a", "b"], ["1", "2"]]}),
    )
    chunks, occ = _chunks(tree, StructuralChunker(max_tokens=200))
    evidence = next(i for i, c in enumerate(chunks) if c.kind == "evidence_unit")
    spans = occ[evidence].spans
    assert len(spans) == 3  # non-contiguous: referring paragraph, caption, table
    assert chunks[evidence].text.startswith("Table 2 lists the days.\n\nTable 2: Days\n\n| a | b |")
    assert all("Table 2 lists" not in c.text for i, c in enumerate(chunks) if i != evidence)


def test_large_table_splits_by_rows_repeating_the_header():
    rows = [["Name", "Days"]] + [[f"person {i}", str(i)] for i in range(40)]
    tree = _tree(RawBlock(kind="table", attrs={"rows": rows}))
    chunks, occ = _chunks(tree, StructuralChunker(max_tokens=60))
    assert len(chunks) > 1
    assert all(c.text.startswith("| Name | Days |\n| --- | --- |") for c in chunks)
    assert all(len(o.spans) == 2 for o in occ)


def test_small_trailing_chunk_merges_into_its_predecessor():
    tree = _tree(
        RawBlock(kind="paragraph", text=" ".join([SENT] * 3)),
        RawBlock(kind="paragraph", text="Tiny."),
    )
    chunks, _ = _chunks(tree, StructuralChunker(max_tokens=60, min_tokens=8))
    assert chunks[-1].text.endswith("Tiny.") and len(chunks) == 1


def test_ids_are_stable_across_versions_and_identical_texts_get_occurrences():
    blocks = [RawBlock(kind="heading", level=1, text="H"), RawBlock(kind="paragraph", text="same")]
    a = build_version(ParsedDoc(blocks=blocks + blocks), "ver_a")
    b = build_version(
        ParsedDoc(blocks=[RawBlock(kind="paragraph", text="new")] + blocks + blocks), "ver_b"
    )
    ch = StructuralChunker(min_tokens=0)
    ids_a = [
        c.id
        for c in materialize(a, ch.draft(a), document_id="d", version_id="ver_a", chunker=ch)[0]
    ]
    ids_b = [
        c.id
        for c in materialize(b, ch.draft(b), document_id="d", version_id="ver_b", chunker=ch)[0]
    ]
    assert len(set(ids_a)) == 2  # two "same" chunks, told apart by occurrence
    assert set(ids_a) <= set(ids_b)


def test_recursive_chunker_spans_are_exact_and_carry_headings():
    tree = _tree(
        RawBlock(kind="heading", level=1, text="Part"),
        *[RawBlock(kind="paragraph", text=SENT) for _ in range(12)],
    )
    chunks, occ = _chunks(tree, RecursiveChunker(max_tokens=50))
    assert len(chunks) > 1 and all(c.token_count <= 50 for c in chunks)
    assert all(c.heading_path == ["Part"] for c in chunks[1:])
    assert all(len(o.spans) == 1 for o in occ)


def test_chunker_fingerprint_and_spec():
    assert (
        StructuralChunker(max_tokens=100).fingerprint()
        != StructuralChunker(max_tokens=200).fingerprint()
    )
    assert isinstance(chunker_from_spec(ChunkerSpec(kind="recursive")), RecursiveChunker)
    assert heading_paths(_tree(RawBlock(kind="paragraph", text="x")))  # every element has a path


def test_an_oversize_paragraph_never_shares_a_chunk_with_its_neighbours():
    """Growing an oversize paragraph must not move the chunk boundaries around it."""
    small = RawBlock(kind="paragraph", text="A short note.")
    big = " ".join([SENT] * 8)

    def chunks(text):
        tree = _tree(
            small,
            RawBlock(kind="paragraph", text=text),
            RawBlock(kind="paragraph", text="Closing remark."),
        )
        return _chunks(tree, StructuralChunker(max_tokens=60, min_tokens=0))[0]

    before = chunks(big)
    assert before[0].text == "A short note." and before[-1].text == "Closing remark."
    after = chunks(big.replace("quick", "very quick", 1))
    changed = {c.text for c in after} - {c.text for c in before}
    assert {before[0].text, before[-1].text} <= {c.text for c in after}  # neighbours untouched
    assert changed and all("Closing" not in t and "short note" not in t for t in changed)
