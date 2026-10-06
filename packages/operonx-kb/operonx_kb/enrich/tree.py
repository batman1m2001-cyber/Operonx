"""The tree index and tree search, as pure functions (track5 §9.5, PLAN E5-E7).

**Nodes.** A version's tree is its root (the document) and its sections. With
headings, the sections are the element tree's. Without any heading, a
document of at least ``toc_min_tokens`` gets **synthesized** sections: a model
reads its blocks, numbered, and answers with a table of contents
(``[{title, first_block, level}]``), which :func:`toc_nodes` turns into spans
(the PageIndex approach, on our elements rather than on pages).

**Summaries** (PLAN E6). A node's summary is written from its own text when
that fits ``summary_input_tokens``, otherwise from its opening and the titles
of what it contains. No node's summary depends on another's, so they are all
asked for at once and an edit re-summarizes only the nodes whose input changed.

**Navigation** (PLAN E7). Tree search shows the navigator the children of the
nodes it is looking at (title, place, summary), numbered; it answers which to
read and whether they are specific enough. :func:`advance` applies an answer.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from operonx_kb.enrich.base import make_request, truncate
from operonx_kb.model.document import CONTAINER_KINDS, Span
from operonx_kb.structure.build import VersionTree
from operonx_kb.text.tokenize import Tokenizer

__all__ = [
    "SUMMARY_VERSION",
    "TOC_VERSION",
    "NodeDraft",
    "heading_nodes",
    "needs_toc",
    "toc_blocks",
    "toc_requests",
    "toc_nodes",
    "summary_request",
    "navigator_request",
    "advance",
]

#: Bump when the summary prompt changes.
SUMMARY_VERSION = "1"
#: Bump when the table-of-contents prompt changes.
TOC_VERSION = "1"
#: How much of each block the table-of-contents model reads.
TOC_BLOCK_TOKENS = 60

SUMMARY_SYSTEM = (
    "You summarize one part of a document for a search system, which reads the summaries "
    "of many parts to decide which ones to open to answer a question.\n"
    "Reply with the summary only: one to three sentences, in the language of the text, saying "
    "what this part covers, with the specific names, terms, numbers and dates it contains."
)

TOC_SYSTEM = (
    "You write the table of contents of a document that has no headings.\n"
    "You are given its blocks (paragraphs, list items, tables), numbered, each cut to its "
    "opening words. Group consecutive blocks into sections by topic and give each section a "
    "short, specific title in the language of the document.\n"
    "Reply with JSON only, in this shape:\n"
    '{"sections": [{"title": "Early life", "first_block": 0, "level": 1}, '
    '{"title": "Education", "first_block": 3, "level": 2}]}\n'
    "Rules:\n"
    "- first_block is the number of the block the section starts at; sections are in order "
    "and the first one starts at the first block shown.\n"
    "- level is 1 for a section, 2 for a subsection of the level-1 section before it.\n"
    "- Prefer a few meaningful sections over one per block."
)

NAVIGATOR_SYSTEM = (
    "You find where the answer to a question is, in the tables of contents of a few "
    "documents. You are shown numbered parts of the documents, each with its document, its "
    "place in the document and a summary.\n"
    "Reply with JSON only, in this shape:\n"
    '{{"choose": [3, 1], "enough": true}}\n'
    "- choose: the numbers of the parts most likely to contain the answer, best first, at "
    "most {beam}.\n"
    "- enough: true when the chosen parts are specific enough to read; false to look at "
    "what is inside them. A part marked (leaf) has nothing inside it."
)


@dataclass
class NodeDraft:
    """A tree node before it has an id and a summary.

    Attributes:
        path: Dotted ordinal path; ``"0"`` is the root.
        source: ``"document"``, ``"heading"`` or ``"toc"``.
    """

    path: str
    depth: int
    title: str
    span: Span
    source: str

    @property
    def parent_path(self) -> Optional[str]:
        return self.path.rsplit(".", 1)[0] if "." in self.path else None


def _root(tree: VersionTree, title: str) -> NodeDraft:
    return NodeDraft(path="0", depth=0, title=tree.title or title, span=(0, len(tree.canonical)), source="document")  # fmt: skip


def heading_nodes(tree: VersionTree, title: str) -> List[NodeDraft]:
    """The root and the element tree's sections, each titled by its heading.

    Args:
        title: The root's title when the version has none (the document key).
    """
    by_id = tree.by_id()
    heading_of: Dict[str, str] = {}
    for e in tree.elements:
        if e.kind == "heading" and e.parent_id and by_id[e.parent_id].kind == "section":
            heading_of.setdefault(e.parent_id, e.text)
    out = [_root(tree, title)]
    path_of: Dict[str, str] = {}
    children: Dict[str, int] = {}
    ordered = sorted(tree.elements, key=lambda e: tuple(int(p) for p in e.path.split(".")))
    for e in ordered:
        if e.kind != "section" or e.span is None or e.span[1] <= e.span[0]:
            continue
        node = by_id.get(e.parent_id) if e.parent_id else None
        while node is not None and node.id not in path_of:
            node = by_id.get(node.parent_id) if node.parent_id else None
        parent = path_of[node.id] if node is not None else "0"
        index = children.get(parent, 0)
        children[parent] = index + 1
        path = f"{parent}.{index}"
        path_of[e.id] = path
        name = heading_of.get(e.id) or tree.canonical[e.span[0] : e.span[1]].split("\n", 1)[0]
        out.append(NodeDraft(path=path, depth=path.count("."), title=name.strip(), span=e.span, source="heading"))  # fmt: skip
    return out


def needs_toc(tree: VersionTree, min_tokens: int, tokenizer: Tokenizer) -> bool:
    """A version without headings, at least ``min_tokens`` long, gets a synthesized ToC."""
    if any(e.kind == "heading" for e in tree.elements):
        return False
    return tokenizer.count(tree.canonical) >= min_tokens


def toc_blocks(tree: VersionTree) -> List[Span]:
    """The spans a table of contents numbers: body leaves with text, title excepted."""
    blocks = [
        e.span
        for e in tree.elements
        if e.layer == "body"
        and e.kind not in CONTAINER_KINDS
        and e.kind != "title"
        and e.span is not None
        and e.span[1] > e.span[0]
    ]
    return sorted(blocks)


def toc_requests(
    tree: VersionTree,
    title: str,
    blocks: Sequence[Span],
    window_tokens: int,
    tokenizer: Tokenizer,
) -> Tuple[List[Dict[str, Any]], List[Tuple[int, int]]]:
    """Table-of-contents requests over windows of numbered blocks.

    Returns:
        The requests, and each one's block range ``(first, last)`` (inclusive).
    """
    lines = [
        f"[{i}] {truncate(tree.canonical[s:e], TOC_BLOCK_TOKENS, tokenizer)}"
        for i, (s, e) in enumerate(blocks)
    ]
    ranges: List[Tuple[int, int]] = []
    used = 0
    for i, line in enumerate(lines):
        n = tokenizer.count(line)
        if ranges and used + n <= window_tokens:
            ranges[-1] = (ranges[-1][0], i)
            used += n
        else:
            ranges.append((i, i))
            used = n
    requests = []
    for first, last in ranges:
        shown = "\n".join(lines[first : last + 1])
        user = f"Document: {tree.title or title}\n\nBlocks {first} to {last}:\n{shown}"
        requests.append(make_request(TOC_SYSTEM, user))
    return requests, ranges


def toc_nodes(
    tree: VersionTree,
    title: str,
    blocks: Sequence[Span],
    ranges: Sequence[Tuple[int, int]],
    answers: Sequence[Any],
) -> Tuple[List[NodeDraft], int]:
    """Tree nodes from the table-of-contents answers of each window.

    An entry is kept when its ``first_block`` is a number inside its window
    and its ``title`` is text; the rest are counted as dropped. A level-2
    entry with no level-1 entry before it is a section of its own. A section
    runs to the next entry of its level or above, or to the end of the text.

    Returns:
        The root and the synthesized sections, and how many entries were dropped.
    """
    entries: List[Tuple[int, int, str]] = []  # (first_block, level, title)
    dropped = 0
    for (first, last), answer in zip(ranges, answers):
        seen = set()
        for item in answer if isinstance(answer, list) else []:
            block = item.get("first_block") if isinstance(item, dict) else None
            name = item.get("title") if isinstance(item, dict) else None
            if not isinstance(block, int) or not first <= block <= last or block in seen:
                dropped += 1
                continue
            if not isinstance(name, str) or not name.strip():
                dropped += 1
                continue
            seen.add(block)
            entries.append((block, 2 if item.get("level") == 2 else 1, name.strip()))
        if not isinstance(answer, list):
            dropped += 1
    entries.sort()
    seen_top = False
    for i, (block, level, name) in enumerate(entries):
        if level == 2 and not seen_top:
            entries[i] = (block, 1, name)
        seen_top = seen_top or entries[i][1] == 1
    end = len(tree.canonical)
    out = [_root(tree, title)]
    top = -1
    sub = 0
    for i, (block, level, name) in enumerate(entries):
        stop = next((blocks[b][0] for b, lv, _ in entries[i + 1 :] if lv <= level), end)
        if level == 1:
            top += 1
            sub = 0
            path = f"0.{top}"
        else:
            path = f"0.{top}.{sub}"
            sub += 1
        out.append(NodeDraft(path=path, depth=level, title=name, span=(blocks[block][0], stop), source="toc"))  # fmt: skip
    return out, dropped


def summary_request(
    tree: VersionTree,
    node: NodeDraft,
    nodes: Sequence[NodeDraft],
    budget: int,
    tokenizer: Tokenizer,
) -> Dict[str, Any]:
    """The request for one node's summary (PLAN E6)."""
    text = tree.canonical[node.span[0] : node.span[1]]
    kids = [n for n in nodes if n.parent_path == node.path]
    if tokenizer.count(text) > budget and kids:
        intro = tree.canonical[node.span[0] : kids[0].span[0]].strip()
        outline = []
        for kid in kids:
            outline.append(f"- {kid.title}")
            outline += [f"  - {g.title}" for g in nodes if g.parent_path == kid.path]
        shown = truncate("\n".join(outline), budget // 2, tokenizer)
        body = f"{truncate(intro, budget // 2, tokenizer)}\n\nIt contains:\n{shown}".strip()
    else:
        body = truncate(text, budget, tokenizer)
    by_path = {n.path: n for n in nodes}
    place, p = [], node.parent_path
    while p is not None and p != "0":
        place.append(by_path[p].title)
        p = by_path[p].parent_path
    where = (
        " > ".join([*reversed(place), node.title]) if node.path != "0" else "(the whole document)"
    )
    user = f"Document: {nodes[0].title}\nPart: {where}\n\n<text>\n{body}\n</text>"
    return make_request(SUMMARY_SYSTEM, user)


def navigator_request(
    question: str, options: Sequence[Dict[str, Any]], beam: int
) -> List[Dict[str, Any]]:
    """The navigator's messages: the question and the numbered options.

    Args:
        options: ``{"n", "document", "place", "summary", "leaf"}`` each.
    """
    lines = []
    for o in options:
        leaf = " (leaf)" if o["leaf"] else ""
        lines.append(
            f"[{o['n']}] {o['document']} — {o['place']}{leaf}\n    {o['summary'] or '(no summary)'}"
        )
    user = f"Question: {question}\n\nParts:\n" + "\n".join(lines)
    return [
        {"role": "system", "content": NAVIGATOR_SYSTEM.format(beam=beam)},
        {"role": "user", "content": user},
    ]


def advance(
    options: Sequence[Dict[str, Any]],
    choose: Any,
    enough: Any,
    picked: Sequence[Dict[str, Any]],
    depth: int,
    beam: int,
    max_depth: int,
) -> Dict[str, Any]:
    """Apply the navigator's answer.

    Chosen leaves are picked; chosen inner nodes are picked too when the answer
    says ``enough`` or this was the last step, otherwise they are the next
    frontier. Numbers that name no option are counted (``invalid``), never
    guessed at; an answer choosing nothing valid ends the search.

    Returns:
        ``frontier`` and ``picked`` (``{"version_id", "node_id"}`` each),
        ``depth``, ``done`` and ``invalid``.
    """
    by_n = {str(o["n"]): o for o in options}
    chosen: List[Dict[str, Any]] = []
    invalid = 0
    for n in choose if isinstance(choose, list) else []:
        o = by_n.get(str(n))
        if o is None or o in chosen:
            invalid += 1
            continue
        chosen.append(o)
    chosen = chosen[:beam]
    last = depth + 1 >= max_depth
    out = list(picked)
    frontier: List[Dict[str, Any]] = []
    for o in chosen:
        ref = {"version_id": o["version_id"], "node_id": o["node_id"]}
        if o["leaf"] or enough is True or last:
            out.append(ref)
        else:
            frontier.append(ref)
    return {"frontier": frontier, "picked": out, "depth": depth + 1,
            "done": not frontier, "invalid": invalid}  # fmt: skip
