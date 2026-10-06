from operonx_kb.parsing.base import PageInfo, ParsedDoc, RawBlock
from operonx_kb.structure.build import build_version, structure_fingerprint, table_markdown


def _build(*blocks, pages=()):
    return build_version(ParsedDoc(blocks=list(blocks), pages=list(pages)), "ver_test")


def test_headings_nest_sections_by_level():
    t = _build(
        RawBlock(kind="heading", level=1, text="A"),
        RawBlock(kind="heading", level=2, text="A.1"),
        RawBlock(kind="paragraph", text="p"),
        RawBlock(kind="heading", level=1, text="B"),
    )
    shape = [(e.path, e.kind, e.text) for e in t.elements if e.kind != "section"]
    assert shape == [
        ("0", "document", t.canonical[3:]),
        ("0.0.0", "heading", "A"),
        ("0.0.1.0", "heading", "A.1"),
        ("0.0.1.1", "paragraph", "p"),
        ("0.1.0", "heading", "B"),
    ]
    assert t.canonical == "## A\n\n### A.1\n\np\n\n## B"


def test_title_resets_sections_and_is_the_version_title():
    t = _build(
        RawBlock(kind="heading", level=1, text="H"),
        RawBlock(kind="title", text="T"),
        RawBlock(kind="paragraph", text="p"),
    )
    assert t.title == "T"
    title = next(e for e in t.elements if e.kind == "title")
    assert title.parent_id == t.elements[0].id


def test_metadata_title_then_first_heading_are_fallbacks():
    assert (
        build_version(
            ParsedDoc(
                blocks=[RawBlock(kind="heading", level=1, text="H")], metadata={"title": "M"}
            ),
            "v",
        ).title
        == "M"
    )
    assert _build(RawBlock(kind="heading", level=2, text="H")).title == "H"


def test_furniture_is_in_the_tree_but_not_the_text():
    t = _build(RawBlock(kind="page_header", text="Header"), RawBlock(kind="paragraph", text="Body"))
    header = next(e for e in t.elements if e.kind == "page_header")
    assert header.layer == "furniture" and header.span is None and header.text == "Header"
    assert t.canonical == "Body"


def test_table_cell_spans_point_at_cells():
    t = _build(RawBlock(kind="table", attrs={"rows": [["a", "b|c"], ["1"]]}))
    table = next(e for e in t.elements if e.kind == "table")
    cells = table.attrs["cell_spans"]
    assert [[t.canonical[s:e] for s, e in row] for row in cells] == [["a", "b\\|c"], ["1", ""]]
    assert table.attrs["rows"] == [["a", "b|c"], ["1", ""]]


def test_table_markdown_shape():
    md, spans = table_markdown([["h1", "h2"], ["x", "y"]])
    assert md == "| h1 | h2 |\n| --- | --- |\n| x | y |"
    assert [md[s:e] for s, e in spans[1]] == ["x", "y"]


def test_empty_blocks_are_dropped_and_empty_tables_too():
    t = _build(
        RawBlock(kind="paragraph", text="  \n "), RawBlock(kind="table", attrs={"rows": [["", ""]]})
    )
    assert t.canonical == "" and [e.kind for e in t.elements] == ["document"]
    assert t.elements[0].span == (0, 0)


def test_caption_pairs_table_below_and_figure_above():
    t = _build(
        RawBlock(kind="figure", text="chart"),
        RawBlock(kind="caption", text="Figure 1"),
        RawBlock(kind="caption", text="Table 1"),
        RawBlock(kind="table", attrs={"rows": [["a"]]}),
    )
    by_text = {e.text: e for e in t.elements}
    table = next(e for e in t.elements if e.kind == "table")
    assert by_text["Figure 1"].attrs["target"] == by_text["chart"].id
    assert by_text["Table 1"].attrs["target"] == table.id
    assert table.attrs["caption"] == by_text["Table 1"].id


def test_content_sha_survives_a_new_version_and_ids_do_not():
    blocks = [RawBlock(kind="paragraph", text="same")]
    a = build_version(ParsedDoc(blocks=blocks), "ver_a")
    b = build_version(ParsedDoc(blocks=blocks), "ver_b")
    assert [e.content_sha for e in a.elements] == [e.content_sha for e in b.elements]
    assert a.elements[1].id != b.elements[1].id


def test_pages_carry_version_id():
    t = _build(
        RawBlock(kind="paragraph", text="x"), pages=[PageInfo(page_no=1, width=595, height=842)]
    )
    assert t.pages[0].version_id == "ver_test" and t.pages[0].page_no == 1


def test_structure_fingerprint_is_stable():
    assert structure_fingerprint() == structure_fingerprint()
