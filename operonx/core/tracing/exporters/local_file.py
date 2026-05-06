"""JsonFileExporter — writes events to a JSON file per request.

Zero external deps. Replaces ``operonx.core.tracing.local.LocalTracer``.
The file path is configurable; default is ``~/.operonx/traces/{request_id}.json``.
Partial flushes append; the final flush rewrites the full file with sorted
events to ensure consistent ordering.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

from operonx.core.tracing.events import TraceEvent


class JsonFileExporter:
    """Append-or-overwrite JSON file exporter.

    Behavior:
        - Each call to ``export()`` writes the current batch to disk.
        - ``metadata["partial"]=True`` triggers append (one JSON-line per
          batch); ``False`` (final flush) writes a single sorted JSON array.
        - Events are serialized via dataclasses.asdict; non-JSON-serializable
          payload values are stringified by ``default=str``.

    The directory is created on first export if missing.
    """

    def __init__(self, directory: Optional[str] = None) -> None:
        self.directory = Path(directory or os.path.expanduser("~/.operonx/traces"))

    def export(
        self,
        events: List[TraceEvent],
        request_id: str,
        metadata: Dict[str, Any],
    ) -> None:
        if not events:
            return
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / f"{request_id}.json"
        partial = bool(metadata.get("partial"))

        # Build the serializable list once.
        serialized = [_serialize(e) for e in events]

        if partial:
            # Append mode — one JSON object per line. Final flush will
            # consolidate into the proper array form.
            with path.open("a", encoding="utf-8") as f:
                for entry in serialized:
                    f.write(json.dumps(entry, default=str))
                    f.write("\n")
            return

        # Final flush: read any partial appends, merge with this batch,
        # write the full sorted array.
        existing: List[Dict[str, Any]] = []
        if path.exists() and path.stat().st_size > 0:
            existing = _read_partial_or_array(path)

        merged = existing + serialized
        # Stable sort by (timestamp, seq) — already the natural order, but
        # explicit makes the final file deterministic.
        merged.sort(key=lambda e: (e.get("timestamp"), e.get("seq", 0)))

        with path.open("w", encoding="utf-8") as f:
            json.dump(merged, f, indent=2, default=str)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _serialize(event: TraceEvent) -> Dict[str, Any]:
    """Convert a TraceEvent to a JSON-friendly dict.

    ``timestamp`` is rendered as ISO-8601 UTC (``...Z``) for cross-runtime
    parity with the Rust port. ``kind`` is kept as the str-enum value.
    """
    d = asdict(event)
    ts = d.get("timestamp")
    if ts is not None:
        d["timestamp"] = ts.isoformat().replace("+00:00", "Z")
    kind = d.get("kind")
    if hasattr(kind, "value"):
        d["kind"] = kind.value
    # ctx is a tuple → list for JSON
    ctx = d.get("ctx")
    if isinstance(ctx, tuple):
        d["ctx"] = list(ctx)
    return d


def _read_partial_or_array(path: Path) -> List[Dict[str, Any]]:
    """Read a file written by either ``partial`` mode (one JSON per line)
    or final mode (array). Tolerates either shape."""
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return []
    if text.startswith("["):
        try:
            data = json.loads(text)
            if isinstance(data, list):
                return data
        except json.JSONDecodeError:
            pass
        return []
    # Fallback: line-delimited JSON from partial appends
    out: List[Dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out
