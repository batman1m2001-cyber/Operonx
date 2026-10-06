"""Multi-hop eval sets for the graph retriever (PLAN §10, track5 §18 P6).

    uv run python scripts/prepare_multihop.py OUT [--questions 400]

Writes ``OUT/<set>/corpus/*.md`` (one paragraph per document: ``# title`` and its
text) and ``OUT/<set>/cases.jsonl`` (``expected.relevant`` = every supporting
paragraph, so Recall@k is the share of a question's hops retrieved), for:

- ``musique``: MuSiQue-Ans v1.0 dev (CC BY 4.0, StonyBrookNLP), answerable questions,
  2-4 hops; 20 paragraphs per question, the supporting ones marked.
- ``2wiki``: 2WikiMultihopQA validation (Apache-2.0, Alab-NII); 10 paragraphs per
  question, the supporting ones named by title.

As in HippoRAG's setup, the paragraphs of the first ``--questions`` questions are
pooled into one corpus (deduplicated by title and text), so every question's
distractors are every other question's paragraphs too. Questions are split by a
seeded shuffle into ``dev`` (tuning, tag ``dev``) and ``test`` (the gate, tag ``test``).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import urllib.request
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

SOURCES = {
    "musique": (
        "https://huggingface.co/datasets/dgslibisey/MuSiQue/resolve/"
        "c8f4f8c9465fb69d31a8eae894c3fd509c4ca321/musique_ans_v1.0_dev.jsonl",
        "musique_ans_v1.0_dev.jsonl",
        "CC BY 4.0 (MuSiQue, StonyBrookNLP)",
    ),
    "2wiki": (
        "https://huggingface.co/datasets/framolfese/2WikiMultihopQA/resolve/"
        "fe713bfbd1afbca1a65246741a75890405d56a3a/data/validation-00000-of-00001.parquet",
        "2wiki_validation.parquet",
        "Apache-2.0 (2WikiMultihopQA, Alab-NII)",
    ),
}
DEV_SHARE = 0.25
SEED = 13


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _download(name: str, cache: Path) -> Path:
    url, file, _ = SOURCES[name]
    path = cache / file
    if not path.exists():
        cache.mkdir(parents=True, exist_ok=True)
        urllib.request.urlretrieve(url, path)
    return path


def _musique(path: Path) -> Iterable[Tuple[str, str, List[Tuple[str, str]], List[int]]]:
    for line in path.read_text("utf-8").splitlines():
        row = json.loads(line)
        paras = [(p["title"], p["paragraph_text"]) for p in row["paragraphs"]]
        gold = [p["idx"] for p in row["paragraphs"] if p["is_supporting"]]
        yield row["id"], row["question"], paras, gold


def _2wiki(path: Path) -> Iterable[Tuple[str, str, List[Tuple[str, str]], List[int]]]:
    import pyarrow.parquet as pq

    for row in pq.read_table(path).to_pylist():
        titles = row["context"]["title"]
        paras = [
            (t, " ".join(s.strip() for s in sents))
            for t, sents in zip(titles, row["context"]["sentences"])
        ]
        wanted = set(row["supporting_facts"]["title"])
        gold = [i for i, t in enumerate(titles) if t in wanted]
        yield row["id"], row["question"], paras, gold


def _key(title: str, text: str) -> str:
    return hashlib.sha256(f"{title}\n{text}".encode()).hexdigest()[:16] + ".md"


def multihop_set(name: str, out: Path, cache: Path, questions: int = 400) -> Dict[str, int]:
    """Write one set; return its counts."""
    path = _download(name, cache)
    rows = (_musique if name == "musique" else _2wiki)(path)
    corpus = out / "corpus"
    corpus.mkdir(parents=True, exist_ok=True)
    picked = []
    for qid, question, paras, gold in rows:
        if len(picked) == questions:
            break
        if len(gold) < 2:  # not multi-hop as labelled
            continue
        picked.append((qid, question, paras, gold))
    order = list(range(len(picked)))
    random.Random(SEED).shuffle(order)
    dev = set(order[: int(len(picked) * DEV_SHARE)])
    docs: Dict[str, str] = {}
    cases = []
    for i, (qid, question, paras, gold) in enumerate(picked):
        for title, text in paras:
            docs.setdefault(_key(title, text), f"# {title}\n\n{text}\n")
        hops = len(gold)
        cases.append({
            "id": f"{name}-{qid}",
            "input": {"query": question, "collection": name, "k": 20},
            "expected": {"relevant": [{"doc_key": _key(*paras[g]), "quote": paras[g][1]} for g in gold]},
            "tags": [name, "dev" if i in dev else "test", f"hops{hops}"],
        })  # fmt: skip
    for key, text in docs.items():
        (corpus / key).write_text(text, encoding="utf-8")
    with (out / "cases.jsonl").open("w", encoding="utf-8") as fh:
        for case in cases:
            fh.write(json.dumps(case, ensure_ascii=False) + "\n")
    url, _, license_ = SOURCES[name]
    (out / "SOURCE.md").write_text(
        f"{name}: {url}\nSHA-256 {_sha(path)}\nLicense: {license_}.\nDerived by "
        f"scripts/prepare_multihop.py: the first {len(picked)} questions with 2+ supporting "
        f"paragraphs; their paragraphs pooled ({len(docs)} documents); dev/test split seed {SEED}.\n",
        encoding="utf-8",
    )
    return {"questions": len(cases), "documents": len(docs), "dev": len(dev)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("out", type=Path)
    parser.add_argument("--questions", type=int, default=400)
    parser.add_argument("--cache", type=Path, default=Path(".operonx/cache/multihop"))
    args = parser.parse_args()
    for set_name in SOURCES:
        print(set_name, multihop_set(set_name, args.out / set_name, args.cache, args.questions))
