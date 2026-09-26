"""The MongoDB store — runs as documents.

Three collections (``runs``, ``op_rollups``, ``records``; a prefix keeps
them apart from a project's own): a summary per run with its fields at the
top level so filters and groups are native queries, a rollup per (run, op),
and each run's full record compressed. The same contract as the SQL
backends, down to the details that are easy to get wrong: an unpriced
group's cost is ``None``, never 0 (Mongo's ``$sum`` of nothing is 0, so
priced runs are counted beside it), and the cursor is an offset.

Uses ``pymongo`` (the ``mongo`` extra). ``client=`` takes an existing
client — a test hands in ``mongomock``.
"""

from __future__ import annotations

import json
import re
import shutil
import zlib
from pathlib import Path
from typing import Any, Dict, List, Optional

from operonx.telemetry.consumers.local import resolve_root

from .base import RunStore
from .model import OpRollup, Page, RunFilter, RunRecord, RunSummary, meta_of_trace, rows_of_trace, summarize

__all__ = ["MongoRunStore"]

_SORTS = {
    "started_desc": [("started_at", -1), ("_id", -1)],
    "started_asc": [("started_at", 1), ("_id", 1)],
    "duration_desc": [("duration_ms", -1), ("_id", -1)],
    # descending puts null after every number: unpriced runs sort last
    "cost_desc": [("cost_usd", -1), ("_id", -1)],
    "errors_desc": [("errors", -1), ("started_at", -1)],
}


class MongoRunStore(RunStore):
    """See the module docstring."""

    def __init__(self, uri: str = "", database: str = "operonx", prefix: str = "", media_dir: Any = "",
                 media_threshold: int = 1024, client: Any = None):
        super().__init__(config={"database": database, "prefix": prefix})
        if client is None:
            if not uri:
                raise ValueError("the mongo run store needs a uri (mongodb://host/…)")
            try:
                import pymongo
            except ImportError as exc:  # pragma: no cover
                raise ImportError('the mongo run store needs: pip install "operonx[mongo]"') from exc
            client = pymongo.MongoClient(uri)
        db = client[database]
        self.runs = db[f"{prefix}runs"]
        self.ops = db[f"{prefix}op_rollups"]
        self.records = db[f"{prefix}records"]
        self.media_dir = Path(media_dir) if media_dir else resolve_root("") / "mongo-media"
        self.media_threshold = int(media_threshold)
        self.runs.create_index([("origin", 1), ("name", 1), ("started_at", -1)])
        self.runs.create_index([("started_at", -1)])
        self.runs.create_index([("job_run", 1)])
        self.ops.create_index([("trace_id", 1)])

    # -- write -----------------------------------------------------------------

    def put_trace(self, trace: Any) -> RunSummary:
        run_media = self.media_dir / str(trace.trace_id)
        media = run_media / "media"
        rows = rows_of_trace(trace, self, media_dir=media, threshold=self.media_threshold)
        if media.is_dir() and not any(media.iterdir()):
            shutil.rmtree(run_media, ignore_errors=True)
        meta = meta_of_trace(trace)
        summary, rollups = summarize(str(trace.trace_id), rows, meta, location=None)
        doc = summary.to_dict()
        doc["_id"] = summary.trace_id
        doc["search"] = " ".join([summary.trace_id, summary.key or "",
                                  json.dumps(summary.metadata or {}, default=str, sort_keys=True)]).lower()
        self.runs.replace_one({"_id": summary.trace_id}, _plain(doc), upsert=True)
        self.ops.delete_many({"trace_id": summary.trace_id})
        if rollups:
            self.ops.insert_many([_plain(r.__dict__.copy()) for r in rollups])
        self.records.replace_one({"_id": summary.trace_id}, {
            "_id": summary.trace_id, "meta": json.dumps(meta, default=str),
            "nodes": zlib.compress(json.dumps(rows, default=str).encode("utf-8"))}, upsert=True)
        return summary

    # -- read ------------------------------------------------------------------

    def list_runs(self, where: Optional[RunFilter] = None, order: str = "started_desc", limit: int = 50,
                  cursor: Optional[str] = None) -> Page:
        if order not in _SORTS:
            raise ValueError(f"unknown order {order!r}; one of {', '.join(_SORTS)}")
        limit = max(1, min(int(limit), 5000))
        offset = int(cursor) if cursor and str(cursor).isdigit() else 0
        q = self._query(where)
        total = self.runs.count_documents(q)
        docs = list(self.runs.find(q).sort(_SORTS[order]).skip(offset).limit(limit))
        items = [_summary(d) for d in docs]
        nxt = offset + len(items)
        return Page(items=items, next_cursor=str(nxt) if nxt < total else None, total=total)

    def get_run(self, trace_id: str) -> Optional[RunRecord]:
        doc = self.runs.find_one({"_id": trace_id})
        rec = self.records.find_one({"_id": trace_id}) if doc else None
        if rec is None:
            return None
        run_media = self.media_dir / trace_id
        return RunRecord(summary=_summary(doc), nodes=json.loads(zlib.decompress(bytes(rec["nodes"])).decode("utf-8")),
                         meta=json.loads(rec.get("meta") or "{}"),
                         media_root=str(run_media) if run_media.is_dir() else None)

    def count(self, where: Optional[RunFilter] = None) -> int:
        return self.runs.count_documents(self._query(where))

    def rollups(self, where: Optional[RunFilter] = None) -> List[OpRollup]:
        ids = [d["_id"] for d in self.runs.find(self._query(where), {"_id": 1})]
        fields = set(OpRollup.__dataclass_fields__)  # type: ignore[attr-defined]
        return [OpRollup(**{k: v for k, v in d.items() if k in fields})
                for d in self.ops.find({"trace_id": {"$in": ids}})]

    def groups(self, where: Optional[RunFilter] = None, by=("origin", "name")) -> List[Dict[str, Any]]:
        from .base import _check_by

        by = _check_by(by)
        pipeline = [
            {"$match": self._query(where)},
            {"$group": {
                "_id": {b: f"${b}" for b in by},
                "runs": {"$sum": 1},
                "errors": {"$sum": {"$cond": [{"$eq": ["$status", "error"]}, 1, 0]}},
                "first_started": {"$min": "$started_at"},
                "last_started": {"$max": "$started_at"},
                "cost_usd": {"$sum": {"$ifNull": ["$cost_usd", 0]}},
                "priced": {"$sum": {"$cond": [{"$eq": [{"$ifNull": ["$cost_usd", None]}, None]}, 0, 1]}},
                "duration_ms": {"$sum": {"$ifNull": ["$duration_ms", 0]}},
            }},
            {"$sort": {"last_started": -1}},
        ]
        out = []
        for g in self.runs.aggregate(pipeline):
            key = g["_id"] or {}
            out.append({**{b: key.get(b) for b in by}, "runs": int(g["runs"]), "errors": int(g["errors"]),
                        "first_started": g["first_started"], "last_started": g["last_started"],
                        # nothing priced is unknown, not $0
                        "cost_usd": g["cost_usd"] if g["priced"] else None,
                        "duration_ms": float(g["duration_ms"] or 0.0)})
        return out

    # -- delete ----------------------------------------------------------------

    def delete_runs(self, where: RunFilter) -> int:
        ids = [d["_id"] for d in self.runs.find(self._query(where), {"_id": 1})]
        if not ids:
            return 0
        self.runs.delete_many({"_id": {"$in": ids}})
        self.ops.delete_many({"trace_id": {"$in": ids}})
        self.records.delete_many({"_id": {"$in": ids}})
        for tid in ids:
            shutil.rmtree(self.media_dir / tid, ignore_errors=True)
        return len(ids)

    # -- helpers ---------------------------------------------------------------

    def _query(self, f: Optional[RunFilter]) -> Dict[str, Any]:
        """A filter as a Mongo query — the same semantics as the SQL one."""
        if f is None:
            return {}
        parts: List[Dict[str, Any]] = []
        for col in ("origin", "name", "status", "version", "job_run", "runbook_run"):
            value = getattr(f, col)
            if value:
                parts.append({col: value})
        span: Dict[str, Any] = {}
        if f.since is not None:
            span["$gte"] = float(f.since)
        if f.until is not None:
            span["$lt"] = float(f.until)
        if span:
            parts.append({"started_at": span})
        if f.trace_ids is not None:
            parts.append({"_id": {"$in": list(f.trace_ids)}})
        for key, value in (f.metadata or {}).items():
            safe = "".join(ch for ch in str(key) if ch.isalnum() or ch in "_-")
            # SQL compares the text form; match the value or its text
            parts.append({"$or": [{f"metadata.{safe}": value}, {f"metadata.{safe}": str(value)}]})
        if f.search:
            parts.append({"search": {"$regex": re.escape(f.search.lower())}})
        if not parts:
            return {}
        return parts[0] if len(parts) == 1 else {"$and": parts}


def _plain(doc: Dict[str, Any]) -> Dict[str, Any]:
    """JSON-safe values only (a metadata value may be anything)."""
    return json.loads(json.dumps(doc, default=str))


def _summary(doc: Dict[str, Any]) -> RunSummary:
    fields = set(RunSummary.__dataclass_fields__)  # type: ignore[attr-defined]
    return RunSummary(**{k: v for k, v in doc.items() if k in fields})
