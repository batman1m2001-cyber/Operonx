"""Read a requirements source in any form into sections of text (plan §4.1).

Every reader returns a :class:`SourceText`: the source's sections, each with
its heading path, its text (paragraphs, list items, tables as ``a | b``
rows, figure captions) and a location a person can find again (``§6.1``,
``p. 3``, ``image``). The extraction checks work on these sections: a
quote must occur in the source's text, and every section must yield a
requirement or be marked descriptive.

- ``.docx`` ``.pptx`` ``.xlsx`` ``.html`` ``.md`` ``.txt``: operonx-kb's
  stdlib parsers. A ``.docx``'s embedded figures are saved as image files,
  in document order, for a session to look at.
- ``.pdf``: operonx-kb's PDF parser (docling-parse); a page with no text
  layer is rendered and read by tesseract.
- images: tesseract (``vie+eng`` by default).
- ``.zip``: extracted into its own empty folder as untrusted data (no
  absolute paths, no ``..``, no links, bounded in size), then each file by
  its type.
"""

from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
import unicodedata
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Dict, List, Optional, Sequence
from xml.etree import ElementTree

from operonx.core.loggings import LOGGER

__all__ = [
    "IMAGE_EXTENSIONS",
    "ReadError",
    "Section",
    "SourceText",
    "normalize",
    "read_source",
]

IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tif", ".tiff", ".webp"})
_KB = frozenset({".docx", ".pptx", ".xlsx", ".xlsm", ".html", ".htm", ".md", ".markdown", ".txt", ".text"})
_MAX_ZIP_BYTES = 500 * 1024 * 1024
_MAX_ZIP_MEMBERS = 5000


class ReadError(RuntimeError):
    """A source could not be read; the message says why."""


@dataclass
class Section:
    id: str  # "§6.1" when the heading is numbered, else "S03"; unique in the source
    title: str
    level: int  # 0: text before the first heading
    path: List[str]  # the heading chain, outermost first
    text: str
    location: str  # where a person finds it: "§6.1 Thu thập tin", "p. 3", "image"
    figures: List[str] = field(default_factory=list)  # image files of figures in this section
    pages: List[int] = field(default_factory=list)  # a PDF's pages it spans
    ocr: bool = False  # its text was read by OCR: quotes are compared loosely (see normalize)

    def to_dict(self) -> Dict:
        return {
            "id": self.id,
            "title": self.title,
            "level": self.level,
            "path": self.path,
            "text": self.text,
            "location": self.location,
            "figures": self.figures,
            "pages": self.pages,
            "ocr": self.ocr,
        }


@dataclass
class SourceText:
    path: str
    kind: str  # docx | pdf | image | zip | markdown | text | html | pptx | xlsx
    sha256: str
    title: str = ""
    sections: List[Section] = field(default_factory=list)
    figures: List[str] = field(default_factory=list)
    members: List["SourceText"] = field(default_factory=list)  # a zip's files
    pages: int = 0
    ocr_pages: List[int] = field(default_factory=list)
    skipped: List[str] = field(default_factory=list)  # a zip's members that could not be read, with why

    def all_sections(self) -> List[Section]:
        """This source's sections, and its members' (a zip), in order."""
        out = list(self.sections)
        for m in self.members:
            out += m.all_sections()
        return out

    @property
    def text(self) -> str:
        return "\n\n".join(f"{s.title}\n{s.text}" if s.title else s.text for s in self.all_sections())


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


_PUNCT_MAP = {"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-"}


def normalize(text: str, loose: bool = False) -> str:
    """For comparing a quote with its source: Unicode NFC, case folded,
    punctuation and symbols as spaces, whitespace collapsed. Diacritics stay:
    "tác động" and "tac dong" are different words. ``loose`` drops them (and
    folds đ to d), for text read by OCR, which misses accents."""
    text = unicodedata.normalize("NFC", text)
    text = "".join(_PUNCT_MAP.get(c, c) for c in text).casefold()
    if loose:
        text = "".join(c for c in unicodedata.normalize("NFD", text) if not unicodedata.combining(c))
        text = text.replace("đ", "d")
    text = "".join(" " if unicodedata.category(c)[0] in "PSZC" else c for c in text)
    return " ".join(text.split())


# ── sections from kb blocks ─────────────────────────────────────────────────

_NUMBERED = re.compile(r"^\s*(\d+(?:\.\d+)*)\.?\s+\S")


def _block_text(block) -> str:
    if block.kind == "table":
        rows = block.attrs.get("rows") or []
        return "\n".join(" | ".join(str(c).strip() for c in row) for row in rows)
    if block.kind == "figure":
        return f"[figure: {block.text}]" if block.text else "[figure]"
    if block.kind == "list_item":
        return f"- {block.text}"
    return block.text


def _sections_from_blocks(blocks: Sequence, figure_files: Sequence[str] = (), paged: bool = False) -> List[Section]:
    sections: List[Section] = []
    chain: List[tuple] = []  # (level, title)
    body: List[str] = []
    figs: List[str] = []
    fig_iter = iter(figure_files)
    used: Dict[str, int] = {}
    current = {"title": "", "level": 0}
    pages_seen: List[int] = []

    def flush() -> None:
        text = "\n".join(t for t in body if t.strip())
        if not text and not current["title"]:
            return
        title = current["title"]
        m = _NUMBERED.match(title)
        sid = f"§{m.group(1)}" if m else f"S{len(sections) + 1:02d}"
        if sid in used:
            used[sid] += 1
            sid = f"{sid}~{used[sid]}"
        else:
            used[sid] = 1
        bare = title[m.end(1) :].lstrip(". ").strip() if m else title
        loc = f"{sid} {bare}".strip() if m else (title or "before the first heading")
        if paged and pages_seen:
            loc += f" (p. {pages_seen[0]}" + (f"–{pages_seen[-1]})" if pages_seen[-1] != pages_seen[0] else ")")
        sections.append(
            Section(sid, title, current["level"], [t for _, t in chain], text, loc, list(figs), list(pages_seen))
        )

    for b in blocks:
        here = [r.page_no for r in (getattr(b, "regions", []) or [])] if paged else []
        if b.kind in ("heading", "title"):
            flush()
            body, figs, pages_seen = [], [], []
            pages_seen += [p for p in here if p not in pages_seen]
            level = b.level or 1
            while chain and chain[-1][0] >= level:
                chain.pop()
            chain.append((level, b.text.strip()))
            current = {"title": b.text.strip(), "level": level}
            continue
        if b.kind in ("page_header", "page_footer"):
            continue
        pages_seen += [p for p in here if p not in pages_seen]
        if b.kind == "figure":
            f = next(fig_iter, None)
            if f:
                figs.append(f)
        body.append(_block_text(b))
    flush()
    return sections


def _docx_figures(path: Path, out_dir: Optional[Path]) -> List[str]:
    """The images a .docx shows, saved in the order the document shows them."""
    if out_dir is None:
        return []
    try:
        z = zipfile.ZipFile(path)
        xml = z.read("word/document.xml")
        rels = ElementTree.fromstring(z.read("word/_rels/document.xml.rels"))
    except (KeyError, zipfile.BadZipFile, ElementTree.ParseError) as exc:
        LOGGER.warning(f"{path.name}: figures not extracted: {exc}")
        return []
    targets = {r.get("Id"): r.get("Target") for r in rels}
    order = re.findall(rb'r:embed="([^"]+)"', xml)
    out_dir.mkdir(parents=True, exist_ok=True)
    files: List[str] = []
    for n, rid in enumerate(order, 1):
        target = targets.get(rid.decode())
        if not target or target.startswith(("http:", "https:")):
            continue
        member = str(PurePosixPath("word") / target) if not target.startswith("/") else target.lstrip("/")
        try:
            data = z.read(member)
        except KeyError:
            continue
        dest = out_dir / f"figure-{n:02d}{PurePosixPath(member).suffix.lower()}"
        dest.write_bytes(data)
        files.append(str(dest))
    return files


def _kb_parse(path: Path):
    from operonx_kb.parsing.router import ParserRouter

    data = path.read_bytes()
    return ParserRouter().for_file(data, name=path.name).parse(data, name=path.name)


def _read_kb(path: Path, out_dir: Optional[Path]) -> SourceText:
    ext = path.suffix.lower()
    doc = _kb_parse(path)
    kind = {".md": "markdown", ".markdown": "markdown", ".txt": "text", ".text": "text", ".htm": "html",
            ".xlsm": "xlsx"}.get(ext, ext.lstrip("."))  # fmt: skip
    figures = _docx_figures(path, out_dir) if ext == ".docx" else []
    sections = _sections_from_blocks(doc.blocks, figures)
    return SourceText(str(path), kind, sha256(path), doc.metadata.get("title", ""), sections, figures)


def _read_pdf(path: Path, languages: str) -> SourceText:
    from operonx_kb.pdf.backend import DoclingParseBackend
    from operonx_kb.pdf.ocr import OcrBackend, TesseractEngine
    from operonx_kb.pdf.parser import PdfParser

    backend = OcrBackend(DoclingParseBackend(), TesseractEngine(languages=languages))
    pages = backend.pages(path.read_bytes())
    parser = PdfParser(backend=_Fixed(pages, backend))
    doc = parser.parse(path.read_bytes(), name=path.name)
    sections = _sections_from_blocks(doc.blocks, paged=True)
    if not any(s.title for s in sections):  # no headings: one section per page
        sections = []
        by_page: Dict[int, List[str]] = {}
        for b in doc.blocks:
            page = b.regions[0].page_no if b.regions else 1
            by_page.setdefault(page, []).append(_block_text(b))
        for page, texts in sorted(by_page.items()):
            sections.append(Section(f"p{page}", "", 0, [], "\n".join(texts), f"p. {page}", pages=[page]))
    ocr = [p.page_no for p in pages if getattr(p, "ocr", False)]
    for s in sections:
        s.ocr = bool(set(s.pages) & set(ocr))
    return SourceText(str(path), "pdf", sha256(path), doc.metadata.get("title", ""), sections, pages=len(pages),
                      ocr_pages=ocr)  # fmt: skip


class _Fixed:
    """A PDF backend that hands back pages already read (OCR done once)."""

    def __init__(self, pages, inner):
        self._pages, self._inner = pages, inner

    def pages(self, data, password=None):
        return self._pages

    def renderer(self, data, password=None):
        return self._inner.renderer(data, password)

    def fingerprint(self):
        return self._inner.fingerprint()


def ocr_image(path: Path, languages: str = "vie+eng") -> str:
    binary = shutil.which("tesseract")
    if binary is None:
        raise ReadError("reading an image needs tesseract: apt install tesseract-ocr tesseract-ocr-vie")
    p = subprocess.run([binary, str(path), "stdout", "-l", languages], capture_output=True, text=True, timeout=300)
    if p.returncode != 0:
        raise ReadError(f"tesseract could not read {path.name}: {p.stderr.strip()[-300:]}")
    return p.stdout


def _read_image(path: Path, languages: str) -> SourceText:
    text = ocr_image(path, languages)
    lines = [ln for ln in text.splitlines()]
    body = "\n".join(lines).strip()
    sec = Section("image", "", 0, [], body, f"image {path.name}", [str(path)], ocr=True)
    return SourceText(str(path), "image", sha256(path), path.name, [sec], [str(path)])


def _safe_extract(path: Path, dest: Path) -> List[Path]:
    """Extract a zip as untrusted data: into *dest*, which must be new or
    empty; nothing outside it, no links, bounded in count and size."""
    if dest.exists() and any(dest.iterdir()):
        raise ReadError(f"{dest} is not empty; a zip is extracted only into an empty folder")
    dest.mkdir(parents=True, exist_ok=True)
    root = dest.resolve()
    out: List[Path] = []
    with zipfile.ZipFile(path) as z:
        infos = z.infolist()
        if len(infos) > _MAX_ZIP_MEMBERS:
            raise ReadError(f"{path.name} has {len(infos)} members (more than {_MAX_ZIP_MEMBERS})")
        if sum(i.file_size for i in infos) > _MAX_ZIP_BYTES:
            raise ReadError(f"{path.name} unpacks to more than {_MAX_ZIP_BYTES // 2**20} MB")
        for info in infos:
            name = info.filename
            mode = (info.external_attr >> 16) & 0o170000
            if mode == 0o120000:
                raise ReadError(f"{path.name}: {name} is a symbolic link; refused")
            parts = PurePosixPath(name).parts
            if name.startswith(("/", "\\")) or ".." in parts or (parts and ":" in parts[0]):
                raise ReadError(f"{path.name}: {name} points outside the folder; refused")
            target = (dest / name).resolve()
            if root not in target.parents and target != root:
                raise ReadError(f"{path.name}: {name} points outside the folder; refused")
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            if parts and (parts[0] == "__MACOSX" or PurePosixPath(name).name.startswith("._")):
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with z.open(info) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)
            out.append(target)
    return out


def read_source(path: Path, out_dir: Optional[Path] = None, languages: str = "vie+eng") -> SourceText:
    """Read *path* by its type. *out_dir* receives what a source holds inside
    it (a docx's figures, a zip's files); it must be a folder of its own."""
    path = Path(path)
    if not path.is_file():
        raise ReadError(f"{path} is not a file")
    ext = path.suffix.lower()
    try:
        if ext == ".zip":
            if out_dir is None:
                raise ReadError("a zip needs out_dir, an empty folder to extract it into")
            files = _safe_extract(path, Path(out_dir) / "files")
            members: List[SourceText] = []
            skipped: List[str] = []
            for f in sorted(files):
                rel = f.relative_to((Path(out_dir) / "files").resolve())
                try:
                    m = read_source(f, Path(out_dir) / "parts" / str(rel).replace("/", "__"), languages)
                except ReadError as exc:
                    skipped.append(f"{rel}: {exc}")
                    continue
                for s in m.all_sections():
                    s.location = f"{rel} · {s.location}"
                members.append(m)
            return SourceText(str(path), "zip", sha256(path), path.name, members=members, skipped=skipped)
        if ext == ".pdf":
            return _read_pdf(path, languages)
        if ext in IMAGE_EXTENSIONS:
            return _read_image(path, languages)
        if ext in _KB:
            return _read_kb(path, out_dir / "figures" if out_dir else None)
    except ReadError:
        raise
    except Exception as exc:  # noqa: BLE001 — a parser's failure names the file
        raise ReadError(f"{path.name}: {type(exc).__name__}: {exc}") from exc
    raise ReadError(f"{path.name}: no reader for {ext or 'files without an extension'}")
