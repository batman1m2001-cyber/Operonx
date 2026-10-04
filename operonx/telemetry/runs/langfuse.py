"""A read-only store over Langfuse — for runs that live only there.

`LangfuseConsumer` ships each execution as an observation carrying what
a local row carries (op name, ``op_full_name``, ``ctx``, status,
duration, upstreams, input and output), so a Langfuse trace reads back
as the same rows a run directory holds, and the same :func:`summarize`
makes its numbers.

Lists come from Langfuse's trace listing (time range and paging are
pushed to the API; the rest of the filter is applied here). Per-op
rollups need every trace's detail, which a remote API cannot afford for
a month of runs — :meth:`rollups` answers for the runs it is asked
about by id only, and a dashboard over many runs belongs on a local or
SQL store.
"""

from __future__ import annotations

import base64
import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any, Dict, Iterator, List, Optional

from .base import RunStore
from .model import OpRollup, Page, RunFilter, RunRecord, RunSummary, summarize

__all__ = ["LangfuseRunStore", "records_of_langfuse_trace"]


def _epoch(stamp: Any) -> float:
    try:
        return datetime.fromisoformat(str(stamp).replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError):
        return 0.0


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def records_of_langfuse_trace(detail: Dict[str, Any]) -> Iterator[Dict[str, Any]]:
    """A Langfuse trace's observations as local-shaped rows."""
    for obs in detail.get("observations") or []:
        meta = obs.get("metadata") or {}
        duration = meta.get("duration_ms")
        if duration is None and obs.get("startTime") and obs.get("endTime"):
            duration = (_epoch(obs["endTime"]) - _epoch(obs["startTime"])) * 1000.0
        status = meta.get("status") or ("error" if obs.get("level") == "ERROR" else "ok")
        start = _epoch(obs.get("startTime"))
        row = {
            "op_id": obs.get("id"),
            "op_name": obs.get("name"),
            "op_full_name": meta.get("op_full_name") or obs.get("name"),
            "start_time": start,
            "end_time": start + float(duration or 0.0) / 1000.0,
            "duration_ms": duration or 0.0,
            "status": status,
            "error": obs.get("statusMessage"),
            "ctx": meta.get("ctx"),
            "is_yield": meta.get("is_yield"),
            "op_type": meta.get("op_type") or "",
            "wall_start": start,
            "inputs": obs.get("input"),
            "outputs": obs.get("output"),
            "upstreams": meta.get("upstreams"),
        }
        # the keys LangfuseConsumer writes only when they are not the default
        for key in ("attempt", "attrs", "inputs_from"):
            if meta.get(key):
                row[key] = meta[key]
        yield row


class LangfuseRunStore(RunStore):
    """See the module docstring."""

    writable = False

    def __init__(self, host: str, public_key: str, secret_key: str, timeout: float = 30.0):
        super().__init__(config={"host": host})
        self.host = str(host).rstrip("/")
        self._auth = base64.b64encode(f"{public_key}:{secret_key}".encode()).decode()
        self.timeout = float(timeout)
        self._detail: Dict[str, Any] = {}  # a finished trace never changes

    def _get(self, path: str, **params: Any) -> Any:
        url = self.host + path
        clean = {k: v for k, v in params.items() if v is not None}
        if clean:
            url += "?" + urllib.parse.urlencode(clean, doseq=True)
        req = urllib.request.Request(url, headers={"Authorization": f"Basic {self._auth}"})
        with urllib.request.urlopen(req, timeout=self.timeout) as res:
            return json.loads(res.read().decode("utf-8"))

    def put_trace(self, trace: Any) -> RunSummary:
        raise NotImplementedError(
            "LangfuseRunStore reads; write with LangfuseConsumer (trace_langfuse:...)"
        )

    def _summary_of_listing(self, t: Dict[str, Any]) -> RunSummary:
        md = dict(t.get("metadata") or {}) if isinstance(t.get("metadata"), dict) else {}
        for tag in t.get("tags") or []:  # tags carry the origin fields as k:v
            k, sep, v = str(tag).partition(":")
            if sep and k not in md:
                md[k] = v
        meta = {
            "workflow_name": t.get("name") or "",
            "wall_started_at": _epoch(t.get("timestamp")),
            "metadata": md,
        }
        if t.get("latency") is not None:
            meta["duration_ms"] = float(t["latency"]) * 1000.0
        s, _ = summarize(str(t["id"]), [], meta, location=None)
        cost = t.get("totalCost")
        if isinstance(cost, (int, float)) and cost:
            s.cost_usd = float(cost)
        return s

    def list_runs(
        self,
        where: Optional[RunFilter] = None,
        order: str = "started_desc",
        limit: int = 50,
        cursor: Optional[str] = None,
    ) -> Page:
        where = where or RunFilter()
        page = int(cursor) if cursor and str(cursor).isdigit() else 1
        listing = self._get(
            "/api/public/traces",
            limit=max(1, min(int(limit), 100)),
            page=page,
            orderBy="timestamp.DESC" if order != "started_asc" else "timestamp.ASC",
            fromTimestamp=_iso(where.since) if where.since is not None else None,
            toTimestamp=_iso(where.until) if where.until is not None else None,
        )
        items = [self._summary_of_listing(t) for t in listing.get("data") or [] if t.get("id")]
        items = [s for s in items if where.matches(s)]
        meta = listing.get("meta") or {}
        total_pages = meta.get("totalPages")
        nxt = str(page + 1) if total_pages and page < int(total_pages) else None
        return Page(items=items, next_cursor=nxt, total=None)

    def get_run(self, trace_id: str) -> Optional[RunRecord]:
        detail = self._detail.get(trace_id)
        if detail is None:
            try:
                detail = self._get(f"/api/public/traces/{urllib.parse.quote(trace_id)}")
            except urllib.error.HTTPError as exc:
                if exc.code == 404:
                    return None  # no such trace; any other failure is the server's, and says so
                raise
            self._detail[trace_id] = detail
        rows = list(records_of_langfuse_trace(detail))
        listing = self._summary_of_listing({**detail, "id": trace_id})
        meta = {
            "workflow_name": detail.get("name") or "",
            "wall_started_at": listing.started_at,
            "metadata": listing.metadata,
        }
        summary, _ = summarize(trace_id, rows, meta, location=None)
        return RunRecord(summary=summary, nodes=rows, meta=meta)

    def rollups(self, where: Optional[RunFilter] = None) -> List[OpRollup]:
        ids = list((where.trace_ids if where else None) or [])
        out: List[OpRollup] = []
        for tid in ids:
            rec = self.get_run(tid)
            if rec is None:
                continue
            _, rolls = summarize(tid, rec.nodes, rec.meta)
            out.extend(rolls)
        return out

    def delete_runs(self, where: RunFilter) -> int:
        return 0  # someone else's server: the studio never reaches in
