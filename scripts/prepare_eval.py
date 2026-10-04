"""Build the K2 eval sets: a corpus folder and a dataset JSONL per set (PLAN R9).

    uv run python scripts/prepare_eval.py xquad vi OUT/xquad_vi      # corpus/ + cases.jsonl
    uv run python scripts/prepare_eval.py xquad en OUT/xquad_en
    uv run python scripts/prepare_eval.py corpus-vi OUT/corpus_vi    # the 200-document corpus
    uv run python scripts/prepare_eval.py corpus-vi --cases-only datasets/corpus_vi.jsonl

**XQuAD** (Artetxe et al., 2020; https://github.com/google-deepmind/xquad), licensed
CC BY-SA 4.0: 240 Wikipedia paragraphs of 48 articles with 1190 questions, the same
in each of its languages. The file is downloaded at a pinned commit and checked by
SHA-256. Each article becomes one HTML document (its title, then its paragraphs);
every third question becomes a case, whose label is the sentence of the paragraph
holding the answer's start (the answer's own span, widened to its sentence so a
short answer like a year is not found all over the article), and whose expected
answer is the answer text. Derived data stays under CC BY-SA 4.0 and is not
committed: this script regenerates it.

**corpus_vi**: the 200-document corpus of ``tests/golden/make_corpus.py`` and its 120
Vietnamese cases, one per fact sentence of a Vietnamese document (``vi_cases``).

A real corpus plugs in the same way: a folder of files ingested with their file
names as keys, and a JSONL of cases ``{"id", "input": {"query", "collection", "k"},
"expected": {"relevant": [{"doc_key", "quote"}], "answer"}}``.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import sys
import urllib.request
from html import escape
from pathlib import Path
from typing import Dict, List

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from operonx_kb.text.sentences import sentence_spans  # noqa: E402

XQUAD_COMMIT = "7d30520c717524000f0d9d2f9c10a069acd9d285"
XQUAD_URL = "https://raw.githubusercontent.com/google-deepmind/xquad/{commit}/xquad.{lang}.json"
XQUAD_SHA256 = {
    "vi": "f619a1eb11fb42d3ab0834259e488a65f585447ef6154437bfb7199d85161a04",
    "en": "e4c57d1c9143aaa1c5d265ba5987a65f4e69528d2a98f29d6e75019b10344f29",
}
XQUAD_LICENSE = "CC BY-SA 4.0 (https://creativecommons.org/licenses/by-sa/4.0/)"


def _write_jsonl(path: Path, rows: List[Dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def download_xquad(lang: str, cache: Path) -> dict:
    """The XQuAD file of ``lang``, from ``cache`` or downloaded, checked by SHA-256."""
    if lang not in XQUAD_SHA256:
        raise SystemExit(f"no pinned XQuAD file for {lang!r}; known: {sorted(XQUAD_SHA256)}")
    path = cache / f"xquad.{lang}.json"
    if not path.exists():
        cache.mkdir(parents=True, exist_ok=True)
        url = XQUAD_URL.format(commit=XQUAD_COMMIT, lang=lang)
        with urllib.request.urlopen(url, timeout=60) as resp:  # noqa: S310 — a pinned https URL
            path.write_bytes(resp.read())
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != XQUAD_SHA256[lang]:
        raise SystemExit(f"{path}: SHA-256 {digest} is not the pinned {XQUAD_SHA256[lang]}")
    return json.loads(path.read_text(encoding="utf-8"))


def _slug(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_")[:40]


def xquad_set(lang: str, out: Path, cache: Path, every: int = 3) -> int:
    """Write ``out/corpus/*.html`` and ``out/cases.jsonl``; return the number of cases."""
    data = download_xquad(lang, cache)["data"]
    collection = f"xquad_{lang}"
    corpus = out / "corpus"
    corpus.mkdir(parents=True, exist_ok=True)
    cases: List[Dict] = []
    n = 0
    for i, article in enumerate(data):
        title = article["title"].replace("_", " ")
        key = f"{i:02d}_{_slug(article['title'])}.html"
        body = "".join(f"<p>{escape(p['context'])}</p>\n" for p in article["paragraphs"])
        html = (f"<!DOCTYPE html><html><head><meta charset='utf-8'><title>{escape(title)}</title>"
                f"</head><body><h1>{escape(title)}</h1>\n{body}</body></html>\n")  # fmt: skip
        (corpus / key).write_text(html, encoding="utf-8")
        for paragraph in article["paragraphs"]:
            context = paragraph["context"]
            spans = sentence_spans(context)
            for qa in paragraph["qas"]:
                n += 1
                if (n - 1) % every:
                    continue
                answer = qa["answers"][0]
                at = answer["answer_start"]
                start, end = next(((s, e) for s, e in spans if s <= at < e), (0, len(context)))
                cases.append({
                    "id": f"xq{lang}-{qa['id']}",
                    "input": {"query": qa["question"], "collection": collection, "k": 20},
                    "expected": {"relevant": [{"doc_key": key, "quote": context[start:end]}],
                                 "answer": answer["text"]},
                    "tags": [lang, "xquad"],
                })  # fmt: skip
    _write_jsonl(out / "cases.jsonl", cases)
    (out / "SOURCE.md").write_text(
        f"XQuAD ({lang}), google-deepmind/xquad at {XQUAD_COMMIT}, SHA-256 "
        f"{XQUAD_SHA256[lang]}.\nLicense: {XQUAD_LICENSE}. Derived by scripts/prepare_eval.py: one "
        "HTML document per article; every third question; label = the answer's sentence.\n",
        encoding="utf-8",
    )
    return len(cases)


def _corpus_module():
    spec = importlib.util.spec_from_file_location(
        "make_corpus", ROOT / "tests" / "golden" / "make_corpus.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def corpus_vi_cases() -> List[Dict]:
    return _corpus_module().vi_cases()


def corpus_vi_set(out: Path) -> int:
    """Write the 200 documents to ``out/corpus`` and their Vietnamese cases to ``out/cases.jsonl``."""
    mod = _corpus_module()
    corpus = out / "corpus"
    corpus.mkdir(parents=True, exist_ok=True)
    for name, data in mod.corpus(200).items():
        (corpus / name).write_bytes(data)
    cases = mod.vi_cases()
    _write_jsonl(out / "cases.jsonl", cases)
    return len(cases)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    x = sub.add_parser("xquad")
    x.add_argument("lang")
    x.add_argument("out", type=Path)
    x.add_argument("--cache", type=Path, default=ROOT / ".operonx" / "cache" / "xquad")
    c = sub.add_parser("corpus-vi")
    c.add_argument("out", type=Path)
    c.add_argument("--cases-only", action="store_true", help="write only the cases, to OUT")
    args = ap.parse_args()
    if args.cmd == "xquad":
        print(f"{xquad_set(args.lang, args.out, args.cache)} cases in {args.out}")
    elif args.cases_only:
        _write_jsonl(args.out, corpus_vi_cases())
        print(f"cases in {args.out}")
    else:
        print(f"{corpus_vi_set(args.out)} cases in {args.out}")


if __name__ == "__main__":
    main()
