"""The first real corpus (D5): Vietnamese public documents with clear licenses.

    uv run python scripts/prepare_vi_public.py OUT                 # corpus/ + cases.jsonl + SOURCE.md
    uv run python scripts/prepare_vi_public.py --refresh-legal     # re-crawl the legal manifest

Two sources, each checked for its license before use:

**MLQA** (Lewis et al., 2020; https://github.com/facebookresearch/MLQA), CC BY-SA 3.0
(stated in the repository's README: "derived from paragraphs in Wikipedia, licensed under
CC-BY-SA 3.0"). Its Vietnamese contexts are paragraphs of Vietnamese Wikipedia, and its
questions were written by people (crowdsourced in English, then translated by professional
translators). ``MLQA_V1.zip`` is downloaded once and checked by SHA-256. Every article of
the ``dev`` split (context-vi, question-vi) becomes one HTML document of its paragraphs, and
every question of it a case: the label is the sentence holding the answer's start in the
paragraph (the dataset's own human-annotated answer span, widened to its sentence), the
expected answer is the answer text. Articles of the ``test`` split that are not in ``dev``
are added as more documents, without cases, so retrieval has distractors.

**Vietnamese legal documents** from the Government's portal (vanban.chinhphu.vn, official
PDFs on datafiles.chinhphu.vn; ``robots.txt``: ``Allow: /``). Legal normative documents and
administrative documents of State agencies are not protected by copyright (Law on
Intellectual Property 2005, Article 15). They are listed with their SHA-256 in
``datasets/vi_public_legal.jsonl`` (committed: URLs and hashes, not the files) and
downloaded from there; a file whose hash changed is refused. They carry no cases: no
human-written legal QA set with a verifiable license was found (UIT-ViQuAD 2.0, BKAI legal
retrieval and the Zalo AI 2021 legal set state no license, or only the uploader's; their
upstream terms could not be verified), and labels are never invented. They are real PDFs
in the same collection, so the eval also measures retrieval among them. Of the 200 files
first crawled (2026-10-04) 137 were scans with no text layer; only files with a text layer
are listed (OCR is out of scope).

News sites (VnExpress, VietnamNet…) are not used: copyrighted, with terms against reuse.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
import urllib.request
import zipfile
from html import escape
from pathlib import Path
from typing import Dict, Iterable, List

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from operonx_kb.text.sentences import sentence_spans  # noqa: E402

MLQA_URL = "https://dl.fbaipublicfiles.com/MLQA/MLQA_V1.zip"
MLQA_SHA256 = "246e8089933d13007fe80684d5c5c0713d6834cf8b3b4a0ec7c66f0a0d2baac8"
MLQA_LICENSE = "CC BY-SA 3.0 (https://creativecommons.org/licenses/by-sa/3.0/legalcode)"
LEGAL_MANIFEST = ROOT / "datasets" / "vi_public_legal.jsonl"
LEGAL_PORTAL = "https://vanban.chinhphu.vn/"
LEGAL_LISTINGS = [LEGAL_PORTAL] + [
    f"{LEGAL_PORTAL}he-thong-van-ban?classid=1&mode=1&{kind}={n}"
    for kind in ("orggroupid", "typegroupid")
    for n in range(1, 7)
]
LEGAL_FILE = re.compile(r"https://datafiles\.chinhphu\.vn/cpp/files/vbpq/[^\"'<>\s]+\.(?:pdf|docx)")
COLLECTION = "vi_public"
UA = {"User-Agent": "operonx-kb eval (https://github.com/batman1m2001-cyber/operonx-kb)"}


def _get(url: str, timeout: int = 120) -> bytes:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 — https, listed sources
        return resp.read()


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_jsonl(path: Path, rows: Iterable[Dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


# ── MLQA ─────────────────────────────────────────────────────────────────────


def mlqa(cache: Path) -> zipfile.ZipFile:
    path = cache / "MLQA_V1.zip"
    if not path.exists():
        cache.mkdir(parents=True, exist_ok=True)
        path.write_bytes(_get(MLQA_URL, timeout=600))
    digest = _sha(path.read_bytes())
    if digest != MLQA_SHA256:
        raise SystemExit(f"{path}: SHA-256 {digest} is not the pinned {MLQA_SHA256}")
    return zipfile.ZipFile(path)


def _articles(z: zipfile.ZipFile, split: str) -> List[dict]:
    name = f"MLQA_V1/{split}/{split}-context-vi-question-vi.json"
    return json.loads(z.read(name))["data"]


def _key(prefix: str, i: int, title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", title.lower().encode("ascii", "ignore").decode()).strip("_")
    return f"{prefix}{i:04d}_{slug[:40] or 'article'}.html"


def _html(title: str, paragraphs: List[str]) -> str:
    body = "".join(f"<p>{escape(p)}</p>\n" for p in paragraphs)
    return (f"<!DOCTYPE html><html><head><meta charset='utf-8'><title>{escape(title)}</title></head>"
            f"<body><h1>{escape(title)}</h1>\n{body}</body></html>\n")  # fmt: skip


def wiki_documents(out: Path, z: zipfile.ZipFile, distractors: int) -> List[Dict]:
    """Write the dev articles (with cases) and ``distractors`` test articles; return the cases."""
    corpus = out / "corpus"
    corpus.mkdir(parents=True, exist_ok=True)
    cases: List[Dict] = []
    dev = _articles(z, "dev")
    titles = set()
    for i, article in enumerate(dev):
        titles.add(article["title"])
        key = _key("wiki_", i, article["title"])
        paragraphs = [p["context"] for p in article["paragraphs"]]
        (corpus / key).write_text(_html(article["title"], paragraphs), encoding="utf-8")
        for p in article["paragraphs"]:
            spans = sentence_spans(p["context"])
            for qa in p["qas"]:
                answer = qa["answers"][0]
                at = answer["answer_start"]
                start, end = next(((s, e) for s, e in spans if s <= at < e), (0, len(p["context"])))
                cases.append({
                    "id": f"mlqa-vi-{qa['id']}",
                    "input": {"query": qa["question"], "collection": COLLECTION, "k": 20},
                    "expected": {"relevant": [{"doc_key": key, "quote": p["context"][start:end]}],
                                 "answer": answer["text"]},
                    "tags": ["vi", "mlqa", "wikipedia"],
                })  # fmt: skip
    extra = sorted(
        (a for a in _articles(z, "test") if a["title"] not in titles), key=lambda a: a["title"]
    )[:distractors]
    for i, article in enumerate(extra):
        paragraphs = list(dict.fromkeys(p["context"] for p in article["paragraphs"]))
        (corpus / _key("wikix_", i, article["title"])).write_text(
            _html(article["title"], paragraphs), encoding="utf-8"
        )
    return cases


# ── legal documents ──────────────────────────────────────────────────────────


def _text_pages(data: bytes) -> int | None:
    """The page count of a PDF with a text layer (at least 50 characters a page), else ``None``."""
    from operonx_kb.pdf.parser import PdfParser

    parsed = PdfParser().parse(data, name="legal.pdf")
    pages = len(parsed.pages)
    chars = sum(len(b.text) for b in parsed.blocks)
    return pages if pages and chars >= 50 * pages else None


def refresh_legal_manifest(path: Path, cache: Path, max_bytes: int, limit: int) -> int:
    """Crawl the portal's listings for document files; write ``{url, file, sha256, bytes}``."""
    urls: List[str] = []
    for listing in LEGAL_LISTINGS:
        html = _get(listing).decode("utf-8", "replace")
        urls += [u for u in LEGAL_FILE.findall(html) if u not in urls]
        time.sleep(1)
    rows, seen = [], set()
    for url in urls:
        if len(rows) >= limit:
            break
        data = _get(url)
        digest = _sha(data)
        if digest in seen or len(data) > max_bytes:
            continue
        seen.add(digest)
        pages = _text_pages(data)
        if pages is None:
            continue  # a scan: no text layer, and OCR is out of scope
        name = f"legal_{len(rows):03d}_{url.rsplit('/', 1)[1]}"
        cache.mkdir(parents=True, exist_ok=True)
        (cache / name).write_bytes(data)
        rows.append(
            {"url": url, "file": name, "sha256": digest, "bytes": len(data), "pages": pages}
        )
        time.sleep(0.5)
    _write_jsonl(path, rows)
    return len(rows)


def legal_documents(out: Path, cache: Path, manifest: Path) -> int:
    corpus = out / "corpus"
    corpus.mkdir(parents=True, exist_ok=True)
    rows = [json.loads(line) for line in manifest.read_text("utf-8").splitlines() if line.strip()]
    for row in rows:
        cached = cache / row["file"]
        if not cached.exists():
            cache.mkdir(parents=True, exist_ok=True)
            cached.write_bytes(_get(row["url"]))
        data = cached.read_bytes()
        if _sha(data) != row["sha256"]:
            raise SystemExit(f"{row['url']}: the file changed (SHA-256); refresh the manifest")
        (corpus / row["file"]).write_bytes(data)
    return len(rows)


def vi_public_set(out: Path, cache: Path, distractors: int = 400) -> Dict[str, int]:
    cases = wiki_documents(out, mlqa(cache), distractors)
    legal = legal_documents(out, cache / "legal", LEGAL_MANIFEST)
    _write_jsonl(out / "cases.jsonl", cases)
    (out / "SOURCE.md").write_text(
        f"MLQA vi (dev: cases; test: {distractors} more articles), {MLQA_URL}, SHA-256 "
        f"{MLQA_SHA256}. License: {MLQA_LICENSE}.\nLegal documents: {legal} files of "
        f"{LEGAL_PORTAL} listed in datasets/vi_public_legal.jsonl (not protected by copyright: "
        "Law on Intellectual Property, Article 15).\n",
        encoding="utf-8",
    )
    return {"cases": len(cases), "legal": legal}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("out", type=Path, nargs="?")
    ap.add_argument("--cache", type=Path, default=ROOT / ".operonx" / "cache" / "vi_public")
    ap.add_argument("--distractors", type=int, default=400)
    ap.add_argument("--refresh-legal", action="store_true", help="re-crawl the legal manifest")
    ap.add_argument("--legal-limit", type=int, default=200)
    ap.add_argument("--legal-max-bytes", type=int, default=3_000_000)
    args = ap.parse_args()
    if args.refresh_legal:
        n = refresh_legal_manifest(
            LEGAL_MANIFEST, args.cache / "legal", args.legal_max_bytes, args.legal_limit
        )
        print(f"{n} legal files in {LEGAL_MANIFEST}")
    if args.out:
        print(vi_public_set(args.out, args.cache, args.distractors), "in", args.out)


if __name__ == "__main__":
    main()
