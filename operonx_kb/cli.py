"""``operonx-kb`` — the command line.

    operonx-kb collections
    operonx-kb create handbook --embedder bge-m3 --store vector_store:kb
    operonx-kb add handbook raw/ --recursive
    operonx-kb list handbook
    operonx-kb status handbook
    operonx-kb delete handbook raw/policy.pdf --purge
    operonx-kb gc handbook --blobs
    operonx-kb verify handbook            # exit 1 when a problem is found
    operonx-kb query handbook "how many days of leave" --mode hybrid --tag hr
    operonx-kb query handbook "how many days of leave" --answer assistant
    operonx-kb eval handbook datasets/handbook.jsonl --mode hybrid

Resources come from ``resources.yaml`` (``--resources`` to point elsewhere).
The dense index is an operonx ``vector_store:``; an in-memory FAISS index
(``dim:`` without ``path:``) lives for one process only, so use pgvector,
Qdrant or a persisted FAISS index for a KB driven from the command line.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import List, Optional, Sequence

import operonx

from operonx_kb.kb import MODES, IngestError, KnowledgeBase, QueryError
from operonx_kb.model.collection import (
    AnalyzerSpec,
    ChunkerSpec,
    CollectionSpec,
    DenseIndexSpec,
    LexicalIndexSpec,
)

__all__ = ["main"]


def _files(paths: Sequence[str], recursive: bool) -> List[Path]:
    out: List[Path] = []
    for raw in paths:
        p = Path(raw)
        if p.is_dir():
            found = p.rglob("*") if recursive else p.glob("*")
            out.extend(sorted(f for f in found if f.is_file() and not f.name.startswith(".")))
        elif p.is_file():
            out.append(p)
        else:
            raise SystemExit(f"operonx-kb: no such file or directory: {raw}")
    return out


def _cmd_collections(kb: KnowledgeBase, args) -> int:
    for c in kb.catalog.list_collections():
        dense = c.spec.dense
        print(f"{c.id:24s} {len(kb.documents(c.id)):5d} docs  "
              f"chunker={c.spec.chunker.kind}/{c.spec.chunker.max_tokens}  "
              f"embedder={dense.embedder if dense else '-'}  store={dense.store if dense else '-'}")  # fmt: skip
    return 0


def _cmd_create(kb: KnowledgeBase, args) -> int:
    spec = CollectionSpec(
        chunker=ChunkerSpec(kind=args.chunker, max_tokens=args.max_tokens),
        dense=DenseIndexSpec(
            embedder=args.embedder, store=args.store, collection=args.store_collection
        )
        if args.embedder
        else None,
        lexical=LexicalIndexSpec(
            index=args.lexical,
            analyzer=AnalyzerSpec(kind=args.analyzer, fold_diacritics=args.fold),
        )
        if args.lexical
        else None,
        language=args.language,
    )
    kb.create_collection(args.collection, spec)
    print(f"created {args.collection}")
    return 0


def _cmd_add(kb: KnowledgeBase, args) -> int:
    files = _files(args.paths, args.recursive)
    if args.key and len(files) != 1:
        raise SystemExit("operonx-kb: --key needs exactly one file")
    failed = 0

    async def run() -> None:
        nonlocal failed
        for f in files:
            try:
                result = await kb.add(args.collection, str(f.resolve()), key=args.key)
                stats = result.get("stats", {})
                chunking = stats.get("chunking", {})
                print(
                    f"{result['action']:6s} {f}  chunks={chunking.get('chunks', '-')} new={chunking.get('new', '-')}"
                )
            except IngestError as exc:
                failed += 1
                print(f"failed {f}: {exc}", file=sys.stderr)

    asyncio.run(run())
    return 1 if failed else 0


def _cmd_list(kb: KnowledgeBase, args) -> int:
    for d in kb.documents(args.collection, include_deleted=args.all):
        state = "deleted" if d.deleted_at else "active"
        print(f"{d.id}  {state:7s}  {d.mime:20.20s}  {d.key}")
    return 0


def _cmd_status(kb: KnowledgeBase, args) -> int:
    docs = kb.documents(args.collection, include_deleted=True)
    live = [d for d in docs if d.active_version_id]
    log = kb.catalog.ingest_log(args.collection)
    actions = {}
    for row in log:
        actions[row["action"]] = actions.get(row["action"], 0) + 1
    print(json.dumps({"collection": args.collection, "documents": len(live), "deleted": len(docs) - len(live),
                      "chunks": len(kb.catalog.active_chunk_ids(collection_id=args.collection)),
                      "ingest_log": actions}, indent=1))  # fmt: skip
    return 0


def _cmd_delete(kb: KnowledgeBase, args) -> int:
    print(json.dumps(asyncio.run(kb.delete(args.collection, args.key, purge=args.purge))))
    return 0


def _cmd_gc(kb: KnowledgeBase, args) -> int:
    print(
        json.dumps(
            asyncio.run(kb.gc(args.collection, blobs=args.blobs, blob_grace_seconds=args.grace))
        )
    )
    return 0


def _cmd_verify(kb: KnowledgeBase, args) -> int:
    report = kb.verify(args.collection)
    print(f"documents={report.documents} elements={report.elements} chunks={report.chunks} "
          f"index_entries={report.index_entries}")  # fmt: skip
    for problem in report.problems:
        print(f"problem: {problem}")
    return 0 if report.ok else 1


def _filter(args) -> Optional[dict]:
    flt = json.loads(args.filter) if args.filter else {}
    if args.tag:
        flt["tags_any"] = args.tag
    if args.acl:
        flt["acl_any"] = args.acl
    return flt or None


def _snippet(text: str, width: int = 100) -> str:
    one = " ".join(text.split())
    return one if len(one) <= width else one[: width - 1] + "…"


def _cmd_query(kb: KnowledgeBase, args) -> int:
    try:
        if args.answer:
            out = asyncio.run(kb.ask(args.collection, args.text, args.answer, filter=_filter(args),
                                     k=args.k, mode=args.mode, reranker=args.rerank))  # fmt: skip
        else:
            out = asyncio.run(kb.search(args.collection, args.text, filter=_filter(args), k=args.k,
                                        mode=args.mode, reranker=args.rerank))  # fmt: skip
    except QueryError as exc:
        print(f"operonx-kb: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=1))
        return 0
    if args.answer:
        print(out["text"])
        for c in out["citations"]:
            pages = f" p.{','.join(map(str, c['pages']))}" if c["pages"] else ""
            print(
                f'  [{c["source"]}] {c["key"]}{pages} span={c["span"]}: "{_snippet(c["quote"], 80)}"'
            )
        for d in out["dropped"]:
            print(f"  dropped: {d['reason']}")
        return 0
    for h in out["hits"]:
        where = " › ".join(h["heading_path"][-2:])
        print(f"{h['rank']:3d}  {h['score']:.4f}  {h['key']}  {where}\n     {_snippet(h['text'])}")
    return 0


def _cmd_eval(kb: KnowledgeBase, args) -> int:
    from operonx_kb.eval import evaluate_answers, evaluate_search

    if args.answers:
        report = asyncio.run(evaluate_answers(kb, args.collection, args.dataset, args.answers,
                                              mode=args.mode, reranker=args.rerank))  # fmt: skip
    else:
        report = asyncio.run(evaluate_search(kb, args.collection, args.dataset, mode=args.mode,
                                             reranker=args.rerank))  # fmt: skip
    report.pop("per_case", None)
    print(json.dumps(report, ensure_ascii=False, indent=1, default=str))
    return 1 if report["errors"] else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="operonx-kb", description="operonx-kb: documents in, provenance kept."
    )
    parser.add_argument("--resources", default="resources.yaml", help="resources.yaml to load")
    parser.add_argument("--catalog", default="kb_catalog:main", help="kb_catalog resource key")
    parser.add_argument("--blob-store", default="kb_blob:main", help="kb_blob resource key")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("collections", help="list collections").set_defaults(fn=_cmd_collections)

    p = sub.add_parser("create", help="create a collection or update its spec")
    p.add_argument("collection")
    p.add_argument("--embedder", help="embedding resource (a bare name means embedding:<name>)")
    p.add_argument(
        "--store", default="vector_store:kb", help="vector_store resource key of the dense index"
    )
    p.add_argument(
        "--store-collection", help="collection inside the vector store (default: the resource's)"
    )
    p.add_argument("--chunker", choices=["structural", "recursive"], default="structural")
    p.add_argument("--max-tokens", type=int, default=400)
    p.add_argument(
        "--lexical", help="kb_lexical resource key of a lexical index, e.g. kb_lexical:main"
    )
    p.add_argument("--analyzer", choices=["simple", "vi"], default="simple")
    p.add_argument("--fold", action="store_true", help="fold diacritics in the lexical index")
    p.add_argument("--language")
    p.set_defaults(fn=_cmd_create)

    p = sub.add_parser("add", help="ingest files or directories")
    p.add_argument("collection")
    p.add_argument("paths", nargs="+")
    p.add_argument("--key", help="document key (one file only; default: the absolute path)")
    p.add_argument("--recursive", action="store_true")
    p.set_defaults(fn=_cmd_add)

    p = sub.add_parser("list", help="list documents")
    p.add_argument("collection")
    p.add_argument("--all", action="store_true", help="include deleted documents")
    p.set_defaults(fn=_cmd_list)

    p = sub.add_parser("status", help="counts and the ingest log summary")
    p.add_argument("collection")
    p.set_defaults(fn=_cmd_status)

    p = sub.add_parser("delete", help="tombstone a document (--purge erases it)")
    p.add_argument("collection")
    p.add_argument("key")
    p.add_argument("--purge", action="store_true")
    p.set_defaults(fn=_cmd_delete)

    p = sub.add_parser(
        "gc", help="remove stale index entries (and unreferenced blobs with --blobs)"
    )
    p.add_argument("collection")
    p.add_argument("--blobs", action="store_true")
    p.add_argument(
        "--grace", type=float, default=3600.0, help="keep blobs younger than this many seconds"
    )
    p.set_defaults(fn=_cmd_gc)

    p = sub.add_parser("verify", help="check spans, blobs and the index; exit 1 on a problem")
    p.add_argument("collection")
    p.set_defaults(fn=_cmd_verify)

    p = sub.add_parser("query", help="search a collection, or answer with --answer LLM")
    p.add_argument("collection")
    p.add_argument("text")
    p.add_argument(
        "--mode",
        choices=list(MODES),
        help="retrieval mode (default: hybrid when the collection has both indexes)",
    )
    p.add_argument("--k", type=int, default=5)
    p.add_argument("--tag", action="append", help="only documents with this tag (repeatable)")
    p.add_argument("--acl", action="append", help="the caller's principal (repeatable)")
    p.add_argument("--filter", help='a KBFilter as JSON, e.g. \'{"fields": {"dept": "hr"}}\'')
    p.add_argument("--rerank", help="reranking resource name")
    p.add_argument("--answer", metavar="LLM", help="answer with this llm resource, with citations")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=_cmd_query)

    p = sub.add_parser("eval", help="evaluate search (or answers) of a collection on a dataset")
    p.add_argument("collection")
    p.add_argument("dataset", help="JSONL of cases with quote-anchored labels")
    p.add_argument("--mode", choices=list(MODES))
    p.add_argument("--rerank", help="reranking resource name")
    p.add_argument("--answers", metavar="LLM", help="evaluate answers of this llm resource")
    p.set_defaults(fn=_cmd_eval)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if not Path(args.resources).is_file():
        raise SystemExit(f"operonx-kb: {args.resources} not found; pass --resources PATH")
    operonx.bootstrap(resources=args.resources)
    kb = KnowledgeBase(catalog=args.catalog, blobs=args.blob_store)
    return args.fn(kb, args)


if __name__ == "__main__":
    sys.exit(main())
