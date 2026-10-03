"""ML layout for PDFs: docling's layout detector and TableFormer behind our seams.

This is the ``layout`` extra (PLAN D3), off by default. :class:`ModelLayout` is
a :class:`~operonx_kb.pdf.layout.LayoutModel` whose regions come from a
:class:`LayoutDetector` and whose table cells come from a
:class:`TableStructurer`; everything after the regions — word assignment,
reading order, heading levels, list markers, cross-column merges — is the same
code the heuristic layout runs, so the two are compared on the regions alone.

Region post-processing ports docling's ``LayoutPostprocessor``
(``utils/layout_postprocessor.py``):

- per-label confidence thresholds (0.45 for headers, title, code, forms and the
  document index; 0.5 for the rest), and ``title`` read as ``section_header``
  (our heading-level step decides which heading is the title);
- overlapping regions (IoU or either containment > 0.8) are grouped and one
  winner kept: a list item beats text of about the same area, code beats what
  it contains, otherwise a candidate loses to a member more than 0.05 more
  confident unless it is over 1.3x larger, and the larger passing candidate wins;
- a key-value region or form loses to a table or picture covering the same
  area; such wrappers are dropped, their words are assigned like any others;
- text regions more than 80% inside a table or picture belong to it;
- a word goes to the region holding the largest share of its box, if that
  share exceeds 0.2; words in no region become one paragraph per line
  (docling's orphan clusters).

Implementations (weights from Hugging Face, pinned by revision; CPU works):

- :class:`HeronDetector`: ``docling-project/docling-layout-heron`` (RT-DETRv2,
  Apache-2.0), run with ``transformers`` as docling's object-detection engine does.
- :class:`TableFormer`: ``docling-ibm-models``' ``TFPredictor`` with the
  ``docling-project/docling-models`` v2.3.0 weights (CDLA-Permissive-2.0).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from operonx_kb.errors import MissingExtraError
from operonx_kb.model.ids import fingerprint
from operonx_kb.pdf.assemble import split_list_marker
from operonx_kb.pdf.backend import BBox, PageRenderer, PdfPage, Word
from operonx_kb.pdf.layout import HeuristicLayout, LayoutBlock, _cluster, set_style

__all__ = [
    "Detection",
    "LayoutDetector",
    "TableStructurer",
    "HeronDetector",
    "TableFormer",
    "ModelLayout",
    "postprocess",
]

_THRESHOLDS = {
    "caption": 0.5, "footnote": 0.5, "formula": 0.5, "list_item": 0.5, "page_footer": 0.5,
    "page_header": 0.5, "picture": 0.5, "table": 0.5, "text": 0.5, "section_header": 0.45,
    "title": 0.45, "code": 0.45, "checkbox_selected": 0.45, "checkbox_unselected": 0.45,
    "form": 0.45, "key_value_region": 0.45, "document_index": 0.45,
}  # fmt: skip
_REMAP = {"title": "section_header"}
_GRAPHICS = frozenset({"table", "document_index", "picture"})
_WRAPPERS = frozenset({"form", "key_value_region"})
_KIND = {
    "caption": "caption", "footnote": "footnote", "formula": "formula", "list_item": "list_item",
    "page_footer": "page_footer", "page_header": "page_header", "picture": "figure",
    "section_header": "heading", "table": "table", "document_index": "table", "text": "paragraph",
    "code": "code", "checkbox_selected": "paragraph", "checkbox_unselected": "paragraph",
}  # fmt: skip


@dataclass
class Detection:
    """A labelled region, in page points (top-left origin)."""

    label: str
    bbox: BBox
    score: float
    words: List[Word] = field(default_factory=list)


class LayoutDetector(ABC):
    """Finds labelled regions on page images."""

    name: str = ""
    version: str = "1"

    def config(self) -> Dict[str, Any]:
        return {}

    def fingerprint(self) -> str:
        cls = type(self)
        return fingerprint(f"{cls.__module__}.{cls.__qualname__}", self.version, self.config())

    @abstractmethod
    def detect(self, images: Sequence[Any], scales: Sequence[float]) -> List[List[Detection]]:
        """Regions of each image, boxes divided by its scale (so in points)."""


class TableStructurer(ABC):
    """Recovers the cell grid of a table region."""

    name: str = ""
    version: str = "1"
    #: Image scale the model wants (docling feeds TableFormer 144 dpi, 2.0).
    scale: float = 2.0

    def config(self) -> Dict[str, Any]:
        return {}

    def fingerprint(self) -> str:
        cls = type(self)
        return fingerprint(f"{cls.__module__}.{cls.__qualname__}", self.version, self.config())

    @abstractmethod
    def structure(
        self, image: Any, page: PdfPage, bbox: BBox, words: List[Word]
    ) -> List[List[str]]:
        """Rows of cell texts for the table at ``bbox`` (points); ``image`` is at :attr:`scale`."""


# ── geometry ─────────────────────────────────────────────────────────────


def _area(b: BBox) -> float:
    return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])


def _inter(a: BBox, b: BBox) -> float:
    return _area((max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])))


def _over_self(a: BBox, b: BBox) -> float:
    """Share of ``a`` inside ``b``."""
    return _inter(a, b) / _area(a) if _area(a) > 0 else 0.0


def _overlapping(a: BBox, b: BBox) -> bool:
    i = _inter(a, b)
    if i <= 0:
        return False
    union = _area(a) + _area(b) - i
    return i / union > 0.8 or i / max(_area(a), 1e-9) > 0.8 or i / max(_area(b), 1e-9) > 0.8


def _groups(dets: List[Detection]) -> List[List[Detection]]:
    parent = list(range(len(dets)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(dets)):
        for j in range(i + 1, len(dets)):
            if _overlapping(dets[i].bbox, dets[j].bbox):
                parent[find(i)] = find(j)
    out: Dict[int, List[Detection]] = defaultdict(list)
    for i, d in enumerate(dets):
        out[find(i)].append(d)
    return list(out.values())


def _best(group: List[Detection], area_threshold: float, conf_threshold: float) -> Detection:
    """docling's _select_best_cluster_from_group."""
    best: Optional[Detection] = None
    for cand in group:
        ok = True
        for other in group:
            if other is cand:
                continue
            ratio = _area(cand.bbox) / max(_area(other.bbox), 1e-9)
            if cand.label == "list_item" and other.label == "text" and abs(1 - ratio) < 0.2:
                continue
            if cand.label == "code" and _over_self(other.bbox, cand.bbox) > 0.8:
                continue
            if ratio <= area_threshold and other.score - cand.score > conf_threshold:
                ok = False
                break
        if not ok:
            continue
        if best is None or (
            _area(cand.bbox) > _area(best.bbox) and best.score - cand.score <= conf_threshold
        ):
            best = cand
    return best or max(group, key=lambda d: d.score)


def postprocess(dets: List[Detection], words: List[Word]) -> Tuple[List[Detection], List[Word]]:
    """docling's region rules (module docstring); returns kept regions with their words, and orphans."""
    kept = [d for d in dets if d.score >= _THRESHOLDS.get(d.label, 0.5)]
    for d in kept:
        d.label = _REMAP.get(d.label, d.label)
    regular = [d for d in kept if d.label not in _GRAPHICS | _WRAPPERS]
    special = [d for d in kept if d.label in _GRAPHICS | _WRAPPERS]
    regular = [_best(g, 1.3, 0.05) for g in _groups(regular)]
    graphics = [
        _best(g, 2.0, 0.3 if g[0].label == "picture" else 0.2)
        for g in _groups([d for d in special if d.label in _GRAPHICS])
    ]
    tables = [d for d in graphics if d.label != "picture"]
    pictures = [
        p for p in graphics if p.label == "picture"
        and not any(_overlapping(p.bbox, t.bbox) and p.score - t.score < 0.1 for t in tables)
    ]  # fmt: skip
    graphics = tables + pictures
    regular = [r for r in regular if not any(_over_self(r.bbox, g.bbox) > 0.8 for g in graphics)]
    orphans: List[Word] = []
    for w in words:
        wb = w.bbox
        holder = next((g for g in graphics if _over_self(wb, g.bbox) > 0.5), None)
        if holder is not None:
            holder.words.append(w)
            continue
        scored = [(_over_self(wb, r.bbox), i) for i, r in enumerate(regular)]
        share, index = max(scored, default=(0.0, -1))
        if share > 0.2:
            regular[index].words.append(w)
        else:
            orphans.append(w)
    out = [r for r in regular if r.words or r.label == "formula"] + graphics
    for d in out:
        if d.words and d.label not in _GRAPHICS:
            d.bbox = (min(w.x0 for w in d.words), min(w.y0 for w in d.words),
                      max(w.x1 for w in d.words), max(w.y1 for w in d.words))  # fmt: skip
    return out, orphans


# ── the models ───────────────────────────────────────────────────────────


class HeronDetector(LayoutDetector):
    """docling's Heron layout detector (RT-DETRv2) on CPU or GPU.

    Args:
        repo_id, revision: The Hugging Face model; the revision is pinned so the
            fingerprint names exactly the weights used.
        threshold: Detection score floor before the per-label thresholds.
        num_threads: CPU threads for torch.
        device: ``"cpu"`` or a torch device string.
    """

    name = "heron"
    version = "1"

    def __init__(
        self,
        repo_id: str = "docling-project/docling-layout-heron",
        revision: str = "8f39ad3c0b4c58e9c2d2c84a38465abf757272d8",
        threshold: float = 0.3,
        num_threads: int = 4,
        device: str = "cpu",
    ):
        self.repo_id, self.revision = repo_id, revision
        self.threshold, self.num_threads, self.device = threshold, num_threads, device
        self._model = None
        self._processor = None

    def config(self) -> Dict[str, Any]:
        return {"repo_id": self.repo_id, "revision": self.revision, "threshold": self.threshold}

    def _load(self) -> None:
        if self._model is not None:
            return
        try:
            import torch
            from huggingface_hub import snapshot_download
            from transformers import AutoImageProcessor, AutoModelForObjectDetection
        except ImportError as exc:
            raise MissingExtraError("The ML layout model", "layout", exc) from exc
        if self.device == "cpu":
            torch.set_num_threads(self.num_threads)
        folder = snapshot_download(self.repo_id, revision=self.revision)
        self._processor = AutoImageProcessor.from_pretrained(folder)
        self._model = AutoModelForObjectDetection.from_pretrained(folder).to(self.device).eval()

    def detect(self, images: Sequence[Any], scales: Sequence[float]) -> List[List[Detection]]:
        import torch

        self._load()
        inputs = self._processor(images=list(images), return_tensors="pt").to(self.device)
        with torch.inference_mode():
            outputs = self._model(**inputs)
        sizes = torch.tensor([[img.height, img.width] for img in images], device=self.device)
        results = self._processor.post_process_object_detection(
            outputs, target_sizes=sizes, threshold=self.threshold
        )
        labels = self._model.config.id2label
        out = []
        for result, scale in zip(results, scales):
            out.append([
                Detection(labels[int(lab)], tuple(float(v) / scale for v in box), float(score))
                for lab, score, box in zip(result["labels"].tolist(), result["scores"].tolist(), result["boxes"].tolist())
            ])  # fmt: skip
        return out


class TableFormer(TableStructurer):
    """TableFormer from ``docling-ibm-models``.

    Args:
        mode: ``"accurate"`` or ``"fast"``.
        revision: ``docling-project/docling-models`` revision (docling pins v2.3.0).
        num_threads: CPU threads.
    """

    name = "tableformer"
    version = "1"
    scale = 2.0

    def __init__(
        self,
        mode: str = "accurate",
        revision: str = "v2.3.0",
        num_threads: int = 4,
        device: str = "cpu",
    ):
        if mode not in ("accurate", "fast"):
            raise ValueError("TableFormer(mode=...) is 'accurate' or 'fast'")
        self.mode, self.revision, self.num_threads, self.device = (
            mode,
            revision,
            num_threads,
            device,
        )
        self._predictor = None

    def config(self) -> Dict[str, Any]:
        return {"mode": self.mode, "revision": self.revision}

    def _load(self) -> None:
        if self._predictor is not None:
            return
        try:
            import docling_ibm_models.tableformer.common as common
            from docling_ibm_models.tableformer.data_management.tf_predictor import TFPredictor
            from huggingface_hub import snapshot_download
        except ImportError as exc:
            raise MissingExtraError("TableFormer", "layout", exc) from exc
        root = snapshot_download(
            "docling-project/docling-models",
            revision=self.revision,
            allow_patterns=["model_artifacts/tableformer/**"],
        )
        folder = f"{root}/model_artifacts/tableformer/{self.mode}"
        config = common.read_config(f"{folder}/tm_config.json")
        config["model"]["save_dir"] = folder
        self._predictor = TFPredictor(config, self.device, self.num_threads)

    def structure(
        self, image: Any, page: PdfPage, bbox: BBox, words: List[Word]
    ) -> List[List[str]]:
        import numpy

        self._load()
        s = self.scale
        tokens = [
            {
                "id": i,
                "text": w.text,
                "bbox": {"l": w.x0 * s, "t": w.y0 * s, "r": w.x1 * s, "b": w.y1 * s},
            }
            for i, w in enumerate(words)
        ]
        page_input = {
            "width": page.width * s,
            "height": page.height * s,
            "image": numpy.asarray(image),
            "tokens": tokens,
        }
        box = [round(bbox[0]) * s, round(bbox[1]) * s, round(bbox[2]) * s, round(bbox[3]) * s]
        out = self._predictor.multi_table_predict(page_input, [box], do_matching=True)[0]
        details = out["predict_details"]
        n_rows, n_cols = int(details.get("num_rows", 0)), int(details.get("num_cols", 0))
        if not n_rows or not n_cols:
            return []
        grid: List[List[List[Word]]] = [[[] for _ in range(n_cols)] for _ in range(n_rows)]
        cells = []
        for cell in out["tf_responses"]:
            b = cell["bbox"]
            cells.append(
                (
                    (b["l"] / s, b["t"] / s, b["r"] / s, b["b"] / s),
                    cell["start_row_offset_idx"],
                    cell["start_col_offset_idx"],
                )
            )
        # A word goes to the predicted cell holding most of it (docling reads cell
        # text back from the page the same way when it does not match cells).
        for w in words:
            share, r, c = max(
                ((_over_self(w.bbox, cb), r, c) for cb, r, c in cells), default=(0.0, 0, 0)
            )
            if share > 0.2 and r < n_rows and c < n_cols:
                grid[r][c].append(w)
        return [
            [" ".join(w.text for w in sorted(ws, key=lambda w: (round(w.y0), w.x0))) for ws in row]
            for row in grid
        ]


class ModelLayout(HeuristicLayout):
    """Layout from an ML region detector, with our reading order and assembly.

    Args:
        detector: Finds regions; default :class:`HeronDetector`.
        tables: Recovers table cells; default :class:`TableFormer`.
        batch_pages: Pages per detector call.
    """

    name = "model"
    version = "1"
    needs_images = True

    def __init__(
        self,
        detector: Optional[LayoutDetector] = None,
        tables: Optional[TableStructurer] = None,
        batch_pages: int = 4,
        **heuristic: Any,
    ):
        super().__init__(**heuristic)
        self.detector = detector or HeronDetector()
        self.tables = tables or TableFormer()
        self.batch_pages = batch_pages

    def config(self) -> Dict[str, Any]:
        return {
            **super().config(),
            "detector": self.detector.fingerprint(),
            "tables": self.tables.fingerprint(),
        }

    def layout(
        self, pages: Sequence[PdfPage], renderer: Optional[PageRenderer] = None
    ) -> List[LayoutBlock]:
        if renderer is None:
            raise ValueError("ModelLayout needs page images: pass a PageRenderer (PdfParser does)")
        detections: List[List[Detection]] = []
        for start in range(0, len(pages), self.batch_pages):
            batch = pages[start : start + self.batch_pages]
            images = [renderer.render(p.page_no, 1.0) for p in batch]
            detections.extend(self.detector.detect(images, [1.0] * len(batch)))
        sizes: Counter = Counter()
        for page in pages:
            for w in page.words:
                sizes[round(w.size * 2) / 2] += len(w.text)
        body = sizes.most_common(1)[0][0] if sizes else 10.0
        blocks: List[LayoutBlock] = []
        for page, dets in zip(pages, detections):
            blocks.extend(self._model_blocks(page, dets, body, renderer))
        self._label_model(blocks, body)
        return self._merge(blocks)

    def _block(self, page: PdfPage, kind: str, words: List[Word], bbox: BBox) -> LayoutBlock:
        segs = self.segments(page, words) if words else []
        lines: Dict[int, List[str]] = defaultdict(list)
        for s in segs:
            lines[s.line].append(s.text)
        block = LayoutBlock(kind=kind, page_no=page.page_no, lines=[" ".join(lines[k]) for k in sorted(lines)],
                            regions=[(page.page_no, bbox)], x0=bbox[0])  # fmt: skip
        set_style(block, segs)
        return block

    def _model_blocks(
        self, page: PdfPage, dets: List[Detection], body: float, renderer: PageRenderer
    ) -> List[LayoutBlock]:
        regions, orphans = postprocess(dets, page.words)
        blocks: List[Tuple[BBox, LayoutBlock]] = []
        table_image = None
        for d in regions:
            kind = _KIND.get(d.label, "paragraph")
            if kind == "table":
                if table_image is None:
                    table_image = renderer.render(page.page_no, self.tables.scale)
                block = self._block(page, "table", [], d.bbox)
                block.rows = (
                    self.tables.structure(table_image, page, d.bbox, d.words) if d.words else []
                )
                if not block.rows:
                    continue
            elif kind == "figure":
                block = self._block(page, "figure", [], d.bbox)
            else:
                block = self._block(page, kind, d.words, d.bbox)
            blocks.append((d.bbox, block))
        for s in self.segments(page, orphans):
            blocks.append((s.bbox, self._block(page, "paragraph", s.words, s.bbox)))
        furniture = [b for _, b in blocks if b.kind in ("page_header", "page_footer")]
        flow = [(bb, b) for bb, b in blocks if b.kind not in ("page_header", "page_footer")]
        furniture_words = {
            id(w)
            for d in regions
            if _KIND.get(d.label) in ("page_header", "page_footer")
            for w in d.words
        }
        body_words = [w for w in page.words if id(w) not in furniture_words]
        gutters = self.gutters(self.segments(page, body_words), body) if body_words else []
        out = list(furniture)
        for block, column in self.order(flow, gutters):
            block.column = column
            out.append(block)
        return out

    def _label_model(self, blocks: List[LayoutBlock], body: float) -> None:
        headings = [b for b in blocks if b.kind == "heading"]
        list_x: Dict[Tuple[int, Tuple[int, int]], List[float]] = defaultdict(list)
        for b in blocks:
            if b.kind == "list_item":
                marker = split_list_marker(b.text)
                b.attrs["ordered"] = bool(marker and marker[1])
                if marker is not None:
                    if marker[1]:
                        b.attrs["marker"] = marker[0]
                    b.lines = [marker[2]]
                list_x[(b.page_no, b.column)].append(b.regions[0][1][0])
        for b in blocks:
            if b.kind == "list_item":
                levels = _cluster(list_x[(b.page_no, b.column)], 3.0)
                b.depth = max(0, sum(1 for x in levels if x < b.regions[0][1][0] - 3.0))
        if headings:
            self._levels(headings, body)
