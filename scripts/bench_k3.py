"""K3 gate: Studio's Knowledge tab on the Vietnamese public corpus (track5 §18 P3).

    uv run --with playwright python scripts/bench_k3.py WORK --studio ../operonx-studio \
        [--pdfs 20 --html 12 --citations 20 --llm gpt-4o-mini --env ../Operon/.env]

1. A collection from the D5 corpus (``prepare_vi_public``, from its download cache): ``--pdfs``
   legal PDFs and ``--html`` Wikipedia articles, chosen with a fixed seed; e5 embeddings in a
   FAISS index saved to disk, and the lexical index (``vi`` + folding).
2. An operonx project around it (``WORK/project``) whose ``kb_admin`` asgi service is
   ``kb_admin_app(llm=...)``, traced to the project's local runs.
3. The project served (``operonx serve --only kb_admin``), and Studio served on another port
   (``--studio-port``) with the project open.
4. Studio's Knowledge tab driven in headless Chromium (playwright): questions written by the
   answer model, each from a seeded random chunk of a PDF, are asked in the Ask page; each
   verified citation's ``[n]`` (or its row, for a second citation behind one marker) is clicked,
   and the viewer is checked: the page shown is the cited page, its image is that page, and the
   lit boxes are exactly the citation's boxes on it. Independently of the KB, pdfium's own text
   layer is read inside those boxes: the quote must be there (whitespace and hyphens aside).
5. Screenshots of every view at desktop (1440x900) and phone (390x844) width, light and dark,
   under ``docs/bench/k3/``.

Results: ``docs/bench/k3.json`` (every sampled citation with its checks); the write-up is
``docs/bench/k3.md``. The answer model's key comes from ``--env`` and is never printed.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import re
import shutil
import socket
import subprocess
import sys
import time
import unicodedata
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import prepare_vi_public  # noqa: E402

EMBEDDER = "intfloat/multilingual-e5-small"
COLLECTION = "vi_public"
SEED = 3
DESKTOP = {"width": 1440, "height": 900}
#: The live Knowledge pane: a revisited screen's last picture is laid over it as a copy without the id.
PANE = "#knowledge"
PHONE = {"width": 390, "height": 844}

QUESTION_PROMPT = (
    "Đây là một đoạn trích từ một văn bản hành chính của Việt Nam:\n\n{passage}\n\n"
    "Hãy viết đúng MỘT câu hỏi bằng tiếng Việt mà đoạn trích này trả lời được, cụ thể, "
    "không nhắc tới 'đoạn trích'. Chỉ trả về câu hỏi."
)


def log(msg: str) -> None:
    sys.stderr.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")
    sys.stderr.flush()


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_port(
    port: int,
    timeout: float,
    proc: Optional[subprocess.Popen] = None,
    log_file: Optional[Path] = None,
) -> None:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if proc is not None and proc.poll() is not None:
            tail = log_file.read_text("utf-8")[-3000:] if log_file else ""
            raise SystemExit(f"the process on :{port} exited ({proc.returncode}):\n{tail}")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return
        except OSError:
            time.sleep(0.3)
    raise SystemExit(f"nothing answered on :{port} after {timeout:.0f} s")


# ── 1. the corpus and the collection ────────────────────────────────────


def pick_corpus(work: Path, cache: Path, pdfs: int, html: int) -> Path:
    """``WORK/corpus``: the chosen files of the D5 corpus (built from the download cache)."""
    picked = work / "corpus"
    if picked.is_dir() and any(picked.iterdir()):
        return picked
    full = work / "vi_public"
    if not (full / "corpus").is_dir():
        prepare_vi_public.vi_public_set(full, cache)
    files = sorted((full / "corpus").iterdir())
    rng = random.Random(SEED)
    chosen = rng.sample([f for f in files if f.suffix == ".pdf"], pdfs)
    chosen += rng.sample([f for f in files if f.name.startswith("wiki_")], html)
    picked.mkdir(parents=True)
    for f in chosen:
        shutil.copy2(f, picked / f.name)
    return picked


RESOURCES = """\
kb_catalog:main:
  path: {kb}/catalog.db
kb_blob:main:
  root: {kb}/blobs
kb_lexical:main:
  path: {kb}/lexical.db
embedding:e5:
  api_type: hf
  model: {embedder}
vector_store:vi_public:
  api_type: faiss
  metric: cosine
{faiss}
"""


def resources(project: Path, faiss: str, llm_block: str) -> str:
    return RESOURCES.format(kb=project / "kb", embedder=EMBEDDER, faiss=faiss) + llm_block


async def ingest(project: Path, corpus: Path, llm_block: str) -> Dict[str, Any]:
    """Ingest the corpus with an in-memory FAISS index, then save the index next to the catalog."""
    import operonx
    from operonx.core.registry import ResourceHub

    from operonx_kb import ChunkerSpec, CollectionSpec, DenseIndexSpec, KnowledgeBase
    from operonx_kb.model.collection import AnalyzerSpec, LexicalIndexSpec

    path = project / "resources.ingest.yaml"
    path.write_text(resources(project, "  dim: 384", llm_block), encoding="utf-8")
    ResourceHub.reset_instance()
    operonx.bootstrap(resources=str(path), env=False)
    kb = KnowledgeBase()
    kb.create_collection(
        COLLECTION,
        CollectionSpec(
            chunker=ChunkerSpec(),
            dense=DenseIndexSpec(embedder="e5", store="vector_store:vi_public", batch_size=32,
                                 passage_template="passage: {text}", query_template="query: {text}"),
            lexical=LexicalIndexSpec(analyzer=AnalyzerSpec(kind="vi", fold_diacritics=True)),
            language="vi",
        ),
    )  # fmt: skip
    started = time.perf_counter()
    files = sorted(p for p in corpus.iterdir() if p.is_file())
    for i, f in enumerate(files):
        await kb.add(COLLECTION, str(f), key=f.name)
        log(f"  ingested {i + 1}/{len(files)} {f.name}")
    report = kb.verify(COLLECTION)
    assert report.ok, report.problems[:3]
    ResourceHub.instance().get("vector_store:vi_public").save(
        str(project / "kb" / "vi_public.faiss")
    )
    return {"documents": len(files), "chunks": report.chunks,
            "seconds": round(time.perf_counter() - started, 1)}  # fmt: skip


KBAPP = '''"""The K3 gate's knowledge base: its admin app and its search flow."""

import operonx
from operonx.kb.admin import kb_admin_app
from operonx.kb.graphs import search_flow  # module level; mode etc. are the run's inputs

operonx.bootstrap()  # ./resources.yaml: operonx serve loads none for an asgi service
APP = kb_admin_app(llm="{llm}")
'''

MANIFEST = """\
[project]
name = "kb_k3"

[tracing]
sinks = ["local"]

[[graph]]
name  = "search_flow"
entry = "kbapp:search_flow"

[[serve]]
name = "kb_admin"
kind = "asgi"
path = "/kb"
host = "127.0.0.1"
port = {port}
app  = "kbapp:APP"
"""


def write_project(project: Path, port: int, llm: str, llm_block: str) -> None:
    (project / "kbapp.py").write_text(KBAPP.format(llm=llm), encoding="utf-8")
    (project / "operonx.toml").write_text(MANIFEST.format(port=port), encoding="utf-8")
    faiss = f"  path: {project / 'kb' / 'vi_public.faiss'}"
    (project / "resources.yaml").write_text(resources(project, faiss, llm_block), encoding="utf-8")
    venv = project / ".venv"
    if not venv.exists():
        venv.symlink_to(ROOT / ".venv")  # the project runs in this checkout's environment


# ── 2. questions from seeded random PDF chunks ──────────────────────────


async def questions(project: Path, llm: str, n: int) -> List[Dict[str, Any]]:
    """``n`` questions, each written by the answer model from one random chunk of a PDF."""
    import operonx
    from operonx.core.registry import ResourceHub

    from operonx_kb import KnowledgeBase

    ResourceHub.reset_instance()
    operonx.bootstrap(resources=str(project / "resources.yaml"), env=False)
    kb = KnowledgeBase()
    chunks = []
    for doc in kb.documents(COLLECTION):
        if doc.mime != "application/pdf":
            continue
        for o in kb.catalog.version_chunks(doc.active_version_id):
            chunks.append((doc.key, o.chunk_id))
    rng = random.Random(SEED)
    rng.shuffle(chunks)
    model = ResourceHub.instance().get(f"llm:{llm}")
    out = []
    for key, chunk_id in chunks:
        chunk = kb.catalog.get_chunks([chunk_id])[chunk_id]
        if chunk.token_count < 40:
            continue
        reply = await model.generate(
            [{"role": "user", "content": QUESTION_PROMPT.format(passage=chunk.text)}]
        )
        q = reply.choices[0].message.content.strip().strip('"')
        out.append({"query": q, "from_key": key, "from_chunk": chunk_id})
        if len(out) == n:
            break
    return out


# ── 3. ground truth: pdfium's text inside the boxes ─────────────────────


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFC", text)
    return re.sub(r"[\s­\-‐‑]+", "", text)


def text_in_boxes(pdf: Path, regions: List[Dict[str, Any]]) -> str:
    """What pdfium reads inside each normalised top-left box, in the boxes' order."""
    import pypdfium2

    doc = pypdfium2.PdfDocument(str(pdf))
    try:
        parts = []
        for r in regions:
            page = doc[r["page_no"] - 1]
            width, height = page.get_size()
            x0, y0, x1, y1 = r["bbox"]
            tp = page.get_textpage()
            parts.append(tp.get_text_bounded(left=x0 * width, bottom=height - y1 * height,
                                             right=x1 * width, top=height - y0 * height))  # fmt: skip
        return "\n".join(parts)
    finally:
        doc.close()


# ── 4. the browser ──────────────────────────────────────────────────────


def shot(page, out: Path, name: str) -> str:
    page.wait_for_selector(".revisit-cover", state="detached")
    path = out / f"{name}.png"
    page.screenshot(path=str(path), full_page=False)
    return str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path)


def open_knowledge(page, base: str, pid: str, theme: str, panel: bool = True) -> None:
    """The project page on the Knowledge tab; ``panel`` false closes the side panel (the
    assistant), as someone who wants the width does."""
    page.goto(f"{base}/p/{pid}")
    page.evaluate(f"localStorage.setItem('ox:theme', '{theme}')")
    if not panel:
        page.evaluate("localStorage.setItem('panelRight', 'false')")
    page.goto(f"{base}/p/{pid}")
    page.wait_for_selector(
        '.tabs button[data-tab="knowledge"]:not([hidden])', state="attached", timeout=60000
    )
    page.evaluate("switchTab('knowledge')")
    page.wait_for_selector(f"{PANE} .evcard", timeout=60000)


def check_citation(page, cite: Dict[str, Any], corpus: Path) -> Dict[str, Any]:
    """After a click: the viewer's page and lit boxes against the citation, and pdfium's text."""
    first = cite["regions"][0]["page_no"]
    settle(page)
    page.wait_for_selector(f'{PANE} .kbpage[data-page="{first}"] .kbcite', timeout=30000)
    page_image_loaded(page)
    shown = page.evaluate("""() => {
        const f = document.querySelector('#knowledge .kbpage');
        const img = f.querySelector('img');
        return {page: Number(f.dataset.page), src: img.getAttribute('src'),
                boxes: [...f.querySelectorAll('.kbcite')].map(d => [d.style.left, d.style.top, d.style.width, d.style.height])};
    }""")
    want = [r["bbox"] for r in cite["regions"] if r["page_no"] == first]
    pc = lambda v: float(str(v).rstrip("%")) / 100  # noqa: E731
    got = [[pc(a), pc(b), pc(a) + pc(c), pc(b) + pc(d)] for a, b, c, d in shown["boxes"]]
    boxes_ok = len(got) == len(want) and all(
        all(abs(x - y) < 1e-4 for x, y in zip(g, w)) for g, w in zip(got, want)
    )
    image_ok = f"/versions/{cite['version_id']}/pages/{first}/image" in shown["src"]
    in_boxes = text_in_boxes(corpus / cite["key"], cite["regions"])
    text_ok = _norm(cite["quote"]) in _norm(in_boxes)
    return {
        "marker": cite["marker"], "key": cite["key"], "quote": cite["quote"], "pages": cite["pages"],
        "regions": cite["regions"], "shown_page": shown["page"], "page_ok": shown["page"] == first,
        "image_ok": image_ok, "boxes_ok": boxes_ok, "text_in_boxes": in_boxes[:600], "text_ok": text_ok,
        "ok": shown["page"] == first and image_ok and boxes_ok and text_ok,
    }  # fmt: skip


def drive(
    base: str, pid: str, qs: List[Dict[str, Any]], corpus: Path, want: int, shots: Path
) -> Dict[str, Any]:
    from playwright.sync_api import sync_playwright

    checked: List[Dict[str, Any]] = []
    texts: List[Dict[str, Any]] = []  # citations into sources without pages
    asked: List[Dict[str, Any]] = []
    screenshots: List[str] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport=DESKTOP)
        open_knowledge(page, base, pid, "light")
        page.click(f'{PANE} .kbviews button:has-text("Ask")')
        for q in qs:
            if len(checked) >= want:
                break
            page.wait_for_selector(f"{PANE} textarea.kbq")
            page.fill(f"{PANE} textarea.kbq", q["query"])
            with page.expect_response(lambda r: r.url.endswith("/query"), timeout=180000) as got:
                page.click(f'{PANE} form.kbask button[type="submit"]')
            res = got.value.json()
            if "answer" not in res or res.get("answer") is None:
                asked.append({**q, "error": res.get("error")})
                continue
            page.wait_for_selector(f"{PANE} .kbanswer", timeout=30000)
            answer = res["answer"]
            cites = [c for c in answer["citations"] if c["regions"]]
            asked.append({**q, "answer": answer["text"], "trace_id": answer["trace_id"],
                          "verified": len(answer["citations"]), "dropped": len(answer["dropped"]),
                          "with_pages": len(cites)})  # fmt: skip
            seen_markers = set()
            for i, c in enumerate(answer["citations"]):
                if len(checked) >= want:
                    break
                if c["marker"] not in seen_markers:
                    seen_markers.add(c["marker"])
                    page.click(f'{PANE} .kbatext .kbmark[data-marker="{c["marker"]}"]')
                    how = "marker"
                else:
                    page.locator(f"{PANE} .kbcite-row").nth(i).click()
                    how = "row"
                if c["regions"]:
                    result = {
                        **check_citation(page, c, corpus),
                        "clicked": how,
                        "query": q["query"],
                    }
                    checked.append(result)
                    log(
                        f"  citation {len(checked)}: [{c['marker']}] {c['key']} "
                        f"p.{c['regions'][0]['page_no']} {'OK' if result['ok'] else 'FAIL'}"
                    )
                    if len(checked) == 1:
                        screenshots.append(shot(page, shots, "gate-first-citation"))
                else:  # a source without pages: the viewer lights the quote in its text
                    settle(page)
                    lit = page.eval_on_selector_all(
                        f"{PANE} .kbband.lit", "els => els.map(e => e.textContent).join('')"
                    )
                    texts.append({"key": c["key"], "quote": c["quote"], "lit": lit, "ok": lit == c["quote"],
                                  "clicked": how, "query": q["query"]})  # fmt: skip
                    log(
                        f"  text citation: [{c['marker']}] {c['key']} {'OK' if lit == c['quote'] else 'FAIL'}"
                    )
                page.click(f"{PANE} .kbback")
                settle(page)
                page.wait_for_selector(f"{PANE} .kbanswer", timeout=30000)
        browser.close()
    return {
        "citations": checked,
        "text_citations": texts,
        "asked": asked,
        "screenshots": screenshots,
    }


def settle(page) -> None:
    """Wait until the Knowledge pane has drawn all of what it shows (``aria-busy`` false)."""
    page.wait_for_selector(f'{PANE}[aria-busy="false"]', timeout=60000)


def page_image_loaded(page) -> None:
    page.wait_for_function(
        "() => { const i = document.querySelector('#knowledge .kbpage img'); "
        "return i && i.complete && i.naturalWidth > 0; }",
        timeout=30000,
    )


def screenshots(base: str, pid: str, shots: Path, sample: Dict[str, Any], query: str) -> List[str]:
    """Every view, at desktop and phone width, light and dark."""
    from playwright.sync_api import sync_playwright

    out: List[str] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        passes = [("desktop", DESKTOP, theme, True) for theme in ("light", "dark")]
        passes += [("phone", PHONE, theme, True) for theme in ("light", "dark")]
        passes.append(("desktop-nopanel", DESKTOP, "light", False))
        for label, size, theme, panel in passes:
            page = browser.new_page(viewport=size, device_scale_factor=1)
            open_knowledge(page, base, pid, theme, panel=panel)
            name = f"{label}-{theme}"
            settle(page)
            out.append(shot(page, shots, f"{name}-documents"))
            page.locator(f"{PANE} .kbdocs .xprow.click", has_text=sample["pdf"]).first.click()
            settle(page)
            page_image_loaded(page)
            out.append(shot(page, shots, f"{name}-viewer"))
            page.locator(f"{PANE} .kbchunkrow").nth(1).click()
            settle(page)
            page.wait_for_selector(f"{PANE} .kbinsphead")
            if label == "phone":
                page.locator(f"{PANE} .kbinspect").scroll_into_view_if_needed()
            out.append(shot(page, shots, f"{name}-chunk"))
            page.click(f"{PANE} .kbback")
            settle(page)
            page.locator(f"{PANE} .kbdocs .xprow.click", has_text=sample["html"]).first.click()
            settle(page)
            out.append(shot(page, shots, f"{name}-text"))
            page.click(f"{PANE} .kbback")
            settle(page)
            page.click(f'{PANE} .kbviews button:has-text("Ask")')
            settle(page)
            page.fill(f"{PANE} textarea.kbq", query)
            for m in ("dense", "lexical", "hybrid"):
                box = page.locator(f"{PANE} .kbmodes label", has_text=m).locator("input")
                if not box.is_checked():
                    box.check()
            with page.expect_response(lambda r: r.url.endswith("/query"), timeout=180000):
                page.click(f'{PANE} form.kbask button[type="submit"]')
            page.wait_for_selector(f"{PANE} .kbanswer")
            out.append(shot(page, shots, f"{name}-ask"))
            page.locator(f"{PANE} .kbcols").scroll_into_view_if_needed()
            out.append(shot(page, shots, f"{name}-ask-hits"))
            marker = page.locator(f"{PANE} .kbatext .kbmark:not([disabled])").first
            if marker.count():
                marker.click()
                settle(page)
                page.wait_for_selector(f"{PANE} .kbcite, {PANE} .kbband.lit")
                if page.locator(f"{PANE} .kbpage").count():
                    page_image_loaded(page)
                page.wait_for_timeout(800)  # the lit box scrolls into view, smoothly
                out.append(shot(page, shots, f"{name}-citation"))
            page.close()
        browser.close()
    return out


# ── main ────────────────────────────────────────────────────────────────


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("work", type=Path)
    ap.add_argument(
        "--studio", type=Path, required=True, help="an operonx-studio checkout with its .venv"
    )
    ap.add_argument("--pdfs", type=int, default=20)
    ap.add_argument("--html", type=int, default=12)
    ap.add_argument("--citations", type=int, default=20)
    ap.add_argument(
        "--questions", type=int, default=40, help="at most this many questions are asked"
    )
    ap.add_argument("--llm", default="gpt-4o-mini")
    ap.add_argument("--llm-resources", type=Path, default=ROOT.parent / "Operon" / "resources.yaml")
    ap.add_argument("--env", type=Path, default=ROOT.parent / "Operon" / ".env")
    ap.add_argument("--studio-port", type=int, default=0)
    ap.add_argument(
        "--cache",
        type=Path,
        default=ROOT / ".operonx" / "cache" / "vi_public",
        help="prepare_vi_public's download cache (MLQA_V1.zip, legal/)",
    )
    ap.add_argument(
        "--operonx",
        type=Path,
        default=None,
        help="an operonx checkout Studio imports first (PYTHONPATH), when its own is older",
    )
    ap.add_argument("--no-screenshots", action="store_true")
    args = ap.parse_args()

    from dotenv import load_dotenv

    load_dotenv(args.env, override=False)  # the answer model's key; never printed
    text = args.llm_resources.read_text("utf-8")
    block = re.search(rf"^llm:{re.escape(args.llm)}:\n(?:[ \t]+.*\n?)+", text, re.M)
    if block is None:
        raise SystemExit(f"no llm:{args.llm} in {args.llm_resources}")
    llm_block = block.group(0)

    work = args.work.resolve()
    project = work / "project"
    (project / "kb").mkdir(parents=True, exist_ok=True)
    corpus = pick_corpus(work, args.cache.resolve(), args.pdfs, args.html)
    results: Dict[str, Any] = {"embedder": EMBEDDER, "llm": args.llm, "seed": SEED,
                               "corpus": sorted(p.name for p in corpus.iterdir())}  # fmt: skip
    if not (project / "kb" / "vi_public.faiss").exists():
        log(f"ingesting {len(results['corpus'])} documents")
        results["ingest"] = asyncio.run(ingest(project, corpus, llm_block))
    kb_port = free_port()
    write_project(project, kb_port, args.llm, llm_block)
    qs_path = work / "questions.json"
    if not qs_path.exists():
        qs_path.write_text(json.dumps(asyncio.run(questions(project, args.llm, args.questions)),
                                      ensure_ascii=False, indent=1), "utf-8")  # fmt: skip
    qs = json.loads(qs_path.read_text("utf-8"))

    procs = []
    try:
        serve_log = work / "serve.log"
        procs.append(subprocess.Popen([str(project / ".venv" / "bin" / "python"), "-m", "operonx.cli.serve",
                                       "--only", "kb_admin"], cwd=project, env=dict(os.environ),
                                      stdout=serve_log.open("wb"), stderr=subprocess.STDOUT))  # fmt: skip
        wait_port(kb_port, 120, procs[-1], serve_log)
        studio_port = args.studio_port or free_port()
        studio_log = work / "studio.log"
        env = {**os.environ, "OPERONX_STUDIO_AUTH": "off", "OPERONX_STUDIO_STATE_DIR": str(work / "studio-state"),
               "OPERONX_STUDIO_RETENTION": "off"}  # fmt: skip
        if args.operonx:
            env["PYTHONPATH"] = str(args.operonx.resolve())
        procs.append(subprocess.Popen([str(args.studio.resolve() / ".venv" / "bin" / "python"), "-m", "operonx_studio.cli",
                                       "--port", str(studio_port), "--no-open"], cwd=work, env=env,
                                      stdout=studio_log.open("wb"), stderr=subprocess.STDOUT))  # fmt: skip
        wait_port(studio_port, 60, procs[-1], studio_log)
        base = f"http://127.0.0.1:{studio_port}"
        req = urllib.request.Request(f"{base}/api/open", data=json.dumps({"path": str(project)}).encode(),
                                     headers={"content-type": "application/json"}, method="POST")  # fmt: skip
        with urllib.request.urlopen(req, timeout=120) as res:
            pid = json.loads(res.read())["id"]
        shots = ROOT / "docs" / "bench" / "k3"
        shots.mkdir(parents=True, exist_ok=True)
        log(
            f"studio {base}/p/{pid}; asking up to {len(qs)} questions for {args.citations} citations"
        )
        got = drive(base, pid, qs, corpus, args.citations, shots)
        results.update(got)
        if not args.no_screenshots:
            sample = {
                "pdf": next(c["key"] for c in got["citations"]),
                "html": next(n for n in results["corpus"] if n.endswith(".html")),
            }
            results["screenshots"] = got["screenshots"] + screenshots(
                base, pid, shots, sample, got["citations"][0]["query"]
            )
    finally:
        for p in procs:
            p.terminate()
            try:
                p.wait(timeout=15)
            except subprocess.TimeoutExpired:
                p.kill()
    cites = results["citations"]
    results["summary"] = {
        "citations": len(cites),
        "ok": sum(c["ok"] for c in cites),
        "page_ok": sum(c["page_ok"] for c in cites),
        "image_ok": sum(c["image_ok"] for c in cites),
        "boxes_ok": sum(c["boxes_ok"] for c in cites),
        "text_ok": sum(c["text_ok"] for c in cites),
        "text_citations": len(results["text_citations"]),
        "text_citations_ok": sum(c["ok"] for c in results["text_citations"]),
        "questions_asked": len(results["asked"]),
        "documents_cited": len({c["key"] for c in cites}),
        "pages_cited": len({(c["key"], c["regions"][0]["page_no"]) for c in cites}),
        "dropped": sum(a.get("dropped", 0) for a in results["asked"]),
        "verified": sum(a.get("verified", 0) for a in results["asked"]),
    }
    (ROOT / "docs" / "bench" / "k3.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=1), "utf-8"
    )
    log(json.dumps(results["summary"]))


if __name__ == "__main__":
    main()
