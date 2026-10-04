"""The pure enrichment functions (PLAN §9): windows, requests, tree nodes, navigation."""

from operonx_kb.chunking import StructuralChunker
from operonx_kb.enrich.base import make_request, request_key, truncate
from operonx_kb.enrich.contextual import context_request, windows, with_context
from operonx_kb.enrich.tree import (
    advance,
    heading_nodes,
    needs_toc,
    summary_request,
    toc_blocks,
    toc_nodes,
    toc_requests,
)
from operonx_kb.model.ids import chunk_id
from operonx_kb.parsing.base import ParsedDoc, RawBlock
from operonx_kb.structure.build import build_version
from operonx_kb.text.tokenize import RegexTokenizer

TOK = RegexTokenizer()


def _tree(blocks, title=None):
    raw = [RawBlock(kind=k, text=t, level=lv) for k, t, lv in blocks]
    if title:
        raw.insert(0, RawBlock(kind="title", text=title))
    return build_version(ParsedDoc(blocks=raw), "ver_1")


def _para(n, words=12):
    return ("paragraph", " ".join(f"w{n}x{i}" for i in range(words)), None)


def test_request_key_is_the_hash_of_the_messages():
    a, b = make_request("sys", "user"), make_request("sys", "user ")
    assert a["key"] == request_key(a["messages"]) and a["key"] != b["key"]


def test_truncate_cuts_at_a_word_boundary_and_marks_the_cut():
    assert truncate("one two three", 10, TOK) == "one two three"
    assert truncate("one two three four", 2, TOK) == "one two …"


def test_windows_pack_a_sections_chunks_and_never_cross_sections():
    tree = _tree([("heading", "A", 1), _para(0), _para(1), _para(2), ("heading", "B", 1), _para(3)])
    chunker = StructuralChunker(max_tokens=14, min_tokens=0)
    drafts = chunker.draft(tree)
    assert len(drafts) == 4
    wins = windows(tree, drafts, window_tokens=26, tokenizer=TOK)
    texts = [tree.canonical[d.spans[0][0] : d.spans[-1][1]] for d in drafts]
    # A's three chunks: two fit one window, the third starts the next; B's is its own.
    assert wins[0] == wins[1] == texts[0] + "\n\n" + texts[1]
    assert wins[2] == texts[2] and wins[3] == texts[3]


def test_a_context_request_puts_the_window_before_the_chunk():
    req = context_request("Guide", ["Guide", "Leave"], "window text", "chunk text")
    user = req["messages"][1]["content"]
    assert user.index("window text") < user.index("chunk text")
    assert "Section: Guide > Leave" in user
    assert (
        with_context(" It covers leave. ", "Guide > Leave\n\nchunk")
        == "It covers leave.\n\nGuide > Leave\n\nchunk"
    )


def test_the_context_input_enters_the_chunk_id_only_when_given():
    plain = chunk_id("doc", "fp", "sha", 0)
    assert chunk_id("doc", "fp", "sha", 0, None) == plain
    assert (
        chunk_id("doc", "fp", "sha", 0, "ctx:a")
        != plain
        != chunk_id("doc", "fp", "sha", 0, "ctx:b")
    )


def test_heading_nodes_nest_like_the_sections():
    tree = _tree(
        [
            ("heading", "One", 1),
            _para(0),
            ("heading", "One.A", 2),
            _para(1),
            ("heading", "Two", 1),
            _para(2),
        ],
        title="Doc",
    )
    nodes = heading_nodes(tree, "key.md")
    assert [(n.path, n.title, n.source) for n in nodes] == [
        ("0", "Doc", "document"), ("0.0", "One", "heading"), ("0.0.0", "One.A", "heading"),
        ("0.1", "Two", "heading"),
    ]  # fmt: skip
    assert nodes[2].parent_path == "0.0" and nodes[0].span == (0, len(tree.canonical))
    assert all(tree.canonical[n.span[0] : n.span[1]].startswith(n.title) for n in nodes[1:])


def test_a_toc_is_needed_only_without_headings_and_from_a_length_on():
    long = _tree([_para(i) for i in range(10)])
    assert needs_toc(long, 50, TOK) and not needs_toc(long, 10_000, TOK)
    assert not needs_toc(_tree([("heading", "H", 1)] + [_para(i) for i in range(10)]), 1, TOK)


def test_toc_requests_split_the_blocks_into_windows():
    tree = _tree([_para(i, words=30) for i in range(6)])
    blocks = toc_blocks(tree)
    requests, ranges = toc_requests(tree, "notes.txt", blocks, window_tokens=70, tokenizer=TOK)
    assert ranges == [(0, 1), (2, 3), (4, 5)] and len(requests) == 3
    assert "Blocks 2 to 3:" in requests[1]["messages"][1]["content"]


def test_toc_nodes_build_spans_and_drop_what_names_no_block():
    tree = _tree([_para(i) for i in range(6)])
    blocks = toc_blocks(tree)
    answers = [
        [{"title": "Start", "first_block": 0}, {"title": "Detail", "first_block": 1, "level": 2},
         {"title": "Bad", "first_block": 9}, {"title": "", "first_block": 2}, "junk"],
        [{"title": "End", "first_block": 3}],
    ]  # fmt: skip
    nodes, dropped = toc_nodes(tree, "t", blocks, [(0, 2), (3, 5)], answers)
    assert dropped == 3
    assert [(n.path, n.title) for n in nodes] == [
        ("0", "t"),
        ("0.0", "Start"),
        ("0.0.0", "Detail"),
        ("0.1", "End"),
    ]
    start, detail, end = nodes[1], nodes[2], nodes[3]
    assert start.span == (blocks[0][0], blocks[3][0]) and detail.span == (
        blocks[1][0],
        blocks[3][0],
    )
    assert end.span == (blocks[3][0], len(tree.canonical))


def test_a_leading_subsection_becomes_a_section():
    tree = _tree([_para(i) for i in range(3)])
    blocks = toc_blocks(tree)
    nodes, _ = toc_nodes(
        tree, "t", blocks, [(0, 2)], [[{"title": "A", "first_block": 0, "level": 2}]]
    )
    assert [(n.path, n.depth) for n in nodes] == [("0", 0), ("0.0", 1)]


def test_a_long_node_is_summarized_from_its_opening_and_outline():
    tree = _tree(
        [("heading", "Big", 1), _para(0)]
        + [x for i in range(1, 5) for x in (("heading", f"Sub {i}", 2), _para(i, 40))],
        title="Doc",
    )
    nodes = heading_nodes(tree, "k")
    big = nodes[1]
    short = summary_request(tree, big, nodes, budget=10_000, tokenizer=TOK)["messages"][1][
        "content"
    ]
    long = summary_request(tree, big, nodes, budget=60, tokenizer=TOK)["messages"][1]["content"]
    assert "w4x39" in short and "w4x39" not in long
    assert "It contains:\n- Sub 1\n- Sub 2" in long and "Part: Big" in long


def _opts():
    return [
        {"n": 1, "version_id": "v", "node_id": "a", "leaf": True},
        {"n": 2, "version_id": "v", "node_id": "b", "leaf": False},
        {"n": 3, "version_id": "v", "node_id": "c", "leaf": False},
    ]


def test_advance_picks_leaves_and_descends_into_inner_nodes():
    out = advance(_opts(), [2, "1", 7, 2], False, [], depth=0, beam=3, max_depth=4)
    assert out["picked"] == [{"version_id": "v", "node_id": "a"}]
    assert out["frontier"] == [{"version_id": "v", "node_id": "b"}]
    assert out["invalid"] == 2 and out["depth"] == 1 and out["done"] is False


def test_advance_stops_on_enough_on_the_last_step_and_on_nothing_valid():
    enough = advance(_opts(), [3], True, [], depth=0, beam=3, max_depth=4)
    assert enough["picked"] == [{"version_id": "v", "node_id": "c"}] and enough["done"]
    last = advance(_opts(), [2, 3], False, [], depth=3, beam=1, max_depth=4)
    assert last["picked"] == [{"version_id": "v", "node_id": "b"}] and last["done"]  # beam 1
    nothing = advance(_opts(), "not a list", None, [], depth=0, beam=3, max_depth=4)
    assert nothing["picked"] == [] and nothing["done"]
