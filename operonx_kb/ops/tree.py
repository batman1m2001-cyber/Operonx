"""Tree search ops: the logic of :func:`operonx_kb.graphs.retrieve.tree_retrieve` (PLAN E7).

The seed retriever's hits name the candidate documents; the navigator (an
``LLMOp`` in the graph) walks their trees a step at a time; the picked nodes
become hits — the chunks inside them first, then the seed's other hits. A
document whose active version has no tree (ingested before the collection had
a ``tree`` spec) is not a candidate; its chunks still arrive through the seed.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from operonx import op

from operonx_kb.enrich import tree as trees
from operonx_kb.model.tree import TreeNode
from operonx_kb.ops._resources import catalog_of
from operonx_kb.stores.catalog.base import Catalog

__all__ = ["tree_candidates", "tree_options", "tree_advance", "tree_hits"]


class _Trees:
    """The trees of a few versions, read once per op."""

    def __init__(self, catalog: Catalog):
        self.catalog = catalog
        self._nodes: Dict[str, Dict[str, TreeNode]] = {}

    def nodes(self, version_id: str) -> Dict[str, TreeNode]:
        if version_id not in self._nodes:
            self._nodes[version_id] = {n.id: n for n in self.catalog.tree_nodes(version_id)}
        return self._nodes[version_id]

    def children(self, version_id: str, node_id: str) -> List[TreeNode]:
        return [n for n in self.nodes(version_id).values() if n.parent_id == node_id]

    def root(self, version_id: str) -> Optional[TreeNode]:
        return next((n for n in self.nodes(version_id).values() if n.parent_id is None), None)


@op(bound="cpu", show_keys="stats")
def tree_candidates(hits: list, collection: str, docs: int, catalog: str) -> dict:
    """The roots of the first ``docs`` documents among the seed's hits that have a tree."""
    cat = catalog_of(catalog)
    active = cat.active_chunks(collection, [h["chunk_id"] for h in hits])
    trees_ = _Trees(cat)
    roots: List[Dict[str, str]] = []
    seen = set()
    untreed = 0
    for h in hits:
        found = active.get(h["chunk_id"])
        if found is None or found.document.id in seen:
            continue
        seen.add(found.document.id)
        root = trees_.root(found.occurrence.version_id)
        if root is None:
            untreed += 1
            continue
        roots.append({"version_id": root.version_id, "node_id": root.id})
        if len(roots) == docs:
            break
    stats = {"documents": len(seen), "roots": len(roots), "untreed": untreed}
    return {"roots": roots, "any": bool(roots), "stats": stats}


@op(bound="cpu", exclude={"trace": ["messages"]}, show_keys="stats")
def tree_options(
    query: str, roots: list, beam: int, catalog: str, frontier: Optional[list] = None
) -> dict:
    """The navigator's next question: the children of the frontier (the roots on the
    first step), numbered; a node with no children is offered itself, as a leaf."""
    trees_ = _Trees(catalog_of(catalog))
    options: List[Dict[str, Any]] = []
    for ref in frontier if frontier is not None else roots:
        version, nodes = ref["version_id"], trees_.nodes(ref["version_id"])
        node = nodes[ref["node_id"]]
        kids = trees_.children(version, node.id)
        root = trees_.root(version)
        for n in kids or [node]:
            place, p = [], n
            while p is not None and p.parent_id is not None:
                place.append(p.title)
                p = nodes.get(p.parent_id)
            options.append({
                "n": len(options) + 1, "version_id": version, "node_id": n.id,
                "document": root.title if root else "", "summary": n.summary,
                "place": " > ".join(reversed(place)) or "(the whole document)",
                "leaf": not trees_.children(version, n.id),
            })  # fmt: skip
    messages = trees.navigator_request(query, options, beam)
    return {"messages": messages, "options": options, "stats": {"options": len(options)}}


@op(show_keys="stats")
def tree_advance(
    options: list,
    beam: int,
    max_depth: int,
    choose: Optional[list] = None,
    enough: Optional[bool] = None,
    picked: Optional[list] = None,
    depth: int = 0,
) -> dict:
    """Apply the navigator's answer (:func:`operonx_kb.enrich.tree.advance`)."""
    out = trees.advance(options, choose, enough, picked or [], depth, beam, max_depth)
    stats = {"depth": out["depth"], "picked": len(out["picked"]), "frontier": len(out["frontier"]),
             "invalid": out["invalid"]}  # fmt: skip
    return {**{k: out[k] for k in ("frontier", "picked", "depth", "done")}, "stats": stats}


@op(bound="cpu", show_keys="stats")
def tree_hits(seed: list, k: int, catalog: str, picked: Optional[list] = None) -> dict:
    """The chunks inside the picked nodes (pick order; inside a node by seed rank,
    then document order), then the seed's other hits, ``k`` in all."""
    cat = catalog_of(catalog)
    trees_ = _Trees(cat)
    seed_rank = {h["chunk_id"]: i for i, h in enumerate(seed)}
    seed_scores = {h["chunk_id"]: h.get("scores") or {} for h in seed}
    occurrences: Dict[str, list] = {}
    order: List[str] = []
    for ref in picked or []:
        version = ref["version_id"]
        node = trees_.nodes(version).get(ref["node_id"])
        if node is None:
            continue
        if version not in occurrences:
            occurrences[version] = cat.version_chunks(version)
        inside = [o for o in occurrences[version] if node.span[0] <= o.spans[0][0] < node.span[1]]
        inside.sort(key=lambda o: (seed_rank.get(o.chunk_id, len(seed)), o.ordinal))
        order += [o.chunk_id for o in inside if o.chunk_id not in order]
    from_tree = len(order)
    order += [h["chunk_id"] for h in seed if h["chunk_id"] not in order]
    hits = [
        {"chunk_id": c, "score": 1.0 / (i + 1), "rank": i + 1, "retriever": "tree",
         "scores": {**seed_scores.get(c, {}), "tree": 1.0 / (i + 1)}}
        for i, c in enumerate(order[:k])
    ]  # fmt: skip
    stats = {"picked": len(picked or []), "from_tree": min(from_tree, k),
             "backfilled": max(0, len(hits) - from_tree), "returned": len(hits)}  # fmt: skip
    return {"hits": hits, "stats": stats}
