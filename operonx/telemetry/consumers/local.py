"""LocalConsumer — generic disk-based V3 workflow-trace consumer.

Writes each run to its own directory under ``<root>``, filed by where
the run came from (its **origin**, see :mod:`operonx.app.origin`), in a
layout designed to be read by humans (``view.txt``) and machines
(``nodes.jsonl``) with zero tooling:

    <root>/                              default: <project>/.operonx/runs
      services/<service>/<YYYY-MM-DD>/<trace_id>/
      jobs/<job>/<job_run>/<trace_id>/
      evals/<job>/<job_run>/<trace_id>/
      playground/<YYYY-MM-DD>/<trace_id>/
      adhoc/<workflow>/<YYYY-MM-DD>/<trace_id>/
        meta.json         — workflow name, timings, metadata (origin…),
                            status ("ok"/"error") and the run's errors
        nodes.jsonl       — source of truth (one OpExecution per line,
                            media offloaded to refs; a generator's later
                            records name the first in ``inputs_from``
                            instead of repeating its inputs)
        view.txt          — human-readable chronological rendering
                            (regeneratable from nodes.jsonl at any time)
        media/            — content-addressed offload store
          <sha256>.<ext>
      latest -> <the most recent run's directory>

``layout: flat`` keeps the old ``<root>/<trace_id>/`` shape; any other
string is a template over ``{origin}``, ``{name}``, ``{group}``,
``{day}``, ``{trace_id}`` and the run's metadata keys. Days are UTC.

Nothing here is callbot-specific — a subclass (`CallbotLocalConsumer`)
overrides `_render_view` to add turn-grouped headers derived from
operonx ctx. See ``docs/TRACING_V3_DESIGN.md`` §4-5.

Concurrency / safety:

* Each trace writes to a per-trace subdirectory → no shared file,
  no locking.
* Content-hashed media dedups across traces (create-exclusive on the
  hash filename, ignore `FileExistsError`).
* Writes go to ``<trace_id>.tmp/`` first; atomic rename on success →
  callers either see a complete directory or nothing.
* TTL cleanup is external (systemd timer or cron) — the consumer just
  writes, it doesn't own retention policy.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, Callable, ClassVar, Dict, List, Optional

from operonx.core.utils.yaml_model import YamlModel
from operonx.core.workflow_trace import OpExecution, WorkflowTrace, format_ctx
from operonx.telemetry.consumer import Consumer
from operonx.telemetry.runs.model import meta_of_trace, row_of

__all__ = ["LocalConsumer", "FORMATTERS", "default_arrow", "resolve_root", "run_path"]


# ---------------------------------------------------------------------------
# Where a run is filed
# ---------------------------------------------------------------------------

#: The folder each origin's runs live in, under the root.
ORIGIN_DIRS = {
    "service": "services",
    "job": "jobs",
    "eval": "evals",
    "playground": "playground",
    "adhoc": "adhoc",
}
_FALLBACK_ROOT = "/tmp/operonx_traces"


def resolve_root(configured: Any = "") -> Path:
    """The directory runs are written under — see ``root`` in
    :class:`LocalConsumer`."""
    import os

    from operonx.core.workflow_trace import active_project

    project = active_project()
    if configured:
        path = Path(str(configured)).expanduser()
        if not path.is_absolute() and project is not None:
            path = Path(project) / path
        return path
    env = os.environ.get("OPERONX_RUNS_DIR")
    if env:
        return Path(env).expanduser()
    if project is not None:
        return Path(project) / ".operonx" / "runs"
    return Path(_FALLBACK_ROOT)


def _safe(part: Any) -> str:
    """One path component: no separators, no parent hops, never empty."""
    text = str(part if part is not None else "").strip()
    text = text.replace("/", "_").replace("\\", "_").replace("\x00", "")
    if text in ("", ".", ".."):
        return "_"
    return text[:120]


def run_path(trace: WorkflowTrace, layout: str = "origin") -> Path:
    """The run's directory relative to the root, from its metadata."""
    import time as _time

    meta = trace.metadata or {}
    origin = str(meta.get("origin") or "adhoc")
    wall = trace.wall_started_at or _time.time()
    day = _time.strftime("%Y-%m-%d", _time.gmtime(wall))
    if origin in ("job", "eval"):
        name, group = meta.get("job") or trace.workflow_name, meta.get("job_run") or day
    elif origin == "service":
        name, group = meta.get("service") or trace.workflow_name, day
    elif origin == "playground":
        name, group = meta.get("service") or trace.workflow_name, day
    else:
        name, group = trace.workflow_name, day
    if layout == "flat":
        return Path(_safe(trace.trace_id))
    if layout == "origin":
        folder = ORIGIN_DIRS.get(origin, _safe(origin))
        if origin == "playground":
            parts = [folder, group, trace.trace_id]
        else:
            parts = [folder, name, group, trace.trace_id]
        return Path(*[_safe(p) for p in parts])
    fields = {k: v for k, v in meta.items() if not isinstance(v, (dict, list))}
    fields.update(origin=origin, name=name, group=group, day=day, trace_id=trace.trace_id)
    try:
        rendered = layout.format(**fields)
    except (KeyError, IndexError, ValueError):
        rendered = f"{origin}/{name}/{group}/{trace.trace_id}"
    return Path(*[_safe(p) for p in rendered.split("/") if p])


# ---------------------------------------------------------------------------
# Per-op arrow-summary formatters — right-hand column of view.txt.
# Apps register their own via config["arrow_formatters"]; the built-in
# `FORMATTERS` dict stays empty by design (universal consumer, no bias).
# ---------------------------------------------------------------------------

# `Formatter = Callable[[OpExecution], str]`
FORMATTERS: Dict[str, Callable[[OpExecution], str]] = {}


def default_arrow(n: OpExecution) -> str:
    """Fallback per-op arrow when nothing custom is registered.

    Just shows the input / output key names — enough to see the shape
    of the op without spamming values.
    """
    return f"{list(n.inputs)}→{list(n.outputs)}"


# ---------------------------------------------------------------------------
# LocalConsumer
# ---------------------------------------------------------------------------


class LocalConsumer(Consumer):
    """Writes one directory per run under `root`.

    Config keys (all optional, sensible defaults):

    * ``root`` (``str | Path``) — base directory. Unset (or empty), it is
      ``$OPERONX_RUNS_DIR``, else ``<project>/.operonx/runs`` — the
      project an `Application` bootstrapped, or the nearest
      ``operonx.toml`` at or above the working directory (a script run
      inside a project) — else ``/tmp/operonx_traces``. A relative root
      resolves against the project root.
    * ``layout`` (``str``) — ``origin`` (default), ``flat``, or a
      template; see the module docstring.
    * ``media_threshold`` (``int``) — bytes; payloads at or above this
      get offloaded to ``media/``. Defaults to ``1024``.
    * ``write_view_txt`` (``bool``) — set False to skip the
      human-readable render (raw ``nodes.jsonl`` only). Defaults to
      ``True``.
    * ``arrow_formatters`` (``dict[str, Formatter]``) — per-op summary
      formatters merged over the built-in ``FORMATTERS``. Any op-name
      not in the map falls back to :func:`default_arrow`.

    Returns the path to the final directory on success.
    """

    DEFAULT_CONFIG: Dict[str, Any] = {
        "root": "",
        "layout": "origin",
        "media_threshold": 1024,
        "write_view_txt": True,
        "arrow_formatters": {},
    }

    def consume(self, trace: WorkflowTrace) -> Path:
        cfg = {**self.DEFAULT_CONFIG, **self.config}
        root = resolve_root(cfg["root"])
        final = root / run_path(trace, cfg["layout"])
        tmp = final.parent / f"{final.name}.tmp"
        media_dir = tmp / "media"

        # Fresh tmp dir on every write — a rerun with the same trace_id
        # replaces the old one atomically.
        if tmp.exists():
            shutil.rmtree(tmp)
        media_dir.mkdir(parents=True, exist_ok=True)

        # 1. meta.json
        (tmp / "meta.json").write_text(
            json.dumps(self._meta_dict(trace), indent=2, default=str),
            encoding="utf-8",
        )

        # 2. nodes.jsonl — one row per execution (`row_of`), values
        # sanitised and offloaded. The row is hand-built rather than
        # `asdict(node)`: asdict deep-copies every value, and real op inputs
        # can hold Cython handles (Triton clients, ONNX sessions) that reject
        # deepcopy; sanitize() strips those to markers, but only if it runs
        # first on the raw value.
        threshold = cfg["media_threshold"]
        with (tmp / "nodes.jsonl").open("w", encoding="utf-8") as f:
            for node in trace.nodes:
                row = row_of(node, trace)
                for key in ("inputs", "outputs", "attrs"):
                    if key in row:
                        row[key] = self.offload_media(self.sanitize(row[key]), media_dir, threshold)
                f.write(json.dumps(row, default=str) + "\n")

        # 3. view.txt — human-readable render (subclasses override
        # `_render_view` for domain-specific structure).
        if cfg["write_view_txt"]:
            (tmp / "view.txt").write_text(
                self._render_view(trace, cfg["arrow_formatters"]), encoding="utf-8"
            )

        # 4. atomic-ish rename + latest symlink
        if final.exists():
            shutil.rmtree(final)
        tmp.rename(final)
        self._update_latest_symlink(root, final.relative_to(root).as_posix())
        return final

    # ------------------------------------------------------------------
    # Overridable render hook — CallbotLocalConsumer replaces this.
    # ------------------------------------------------------------------

    def _render_view(self, trace: WorkflowTrace, extra_formatters: dict) -> str:
        """Generic flat chronological render.

        Nodes sorted by `start_time`, one row per op, ctx column shows
        which invocation. Blank lines separate ctx groups — a rough
        visual chunker for downstream readers. Below each row, two
        indented lines show compact k=v for inputs / outputs (large
        values collapsed via :meth:`_format_value`). Toggle off with
        ``config["show_io"] = False`` for a one-line-per-op view.
        """
        formatters = {**FORMATTERS, **(extra_formatters or {})}
        show_io = self.config.get("show_io", True)
        header = self._render_header(trace)

        rows: List[str] = []
        prev_ctx_root: Optional[str] = None
        base_time = trace.started_at
        for node in sorted(trace.nodes, key=lambda n: n.start_time):
            top_ctx = node.ctx[1] if len(node.ctx) >= 2 else node.ctx[0]
            if prev_ctx_root is not None and top_ctx != prev_ctx_root:
                rows.append("")  # blank line between yield-groups
            prev_ctx_root = top_ctx

            t_offset = node.start_time - base_time
            arrow_fn = formatters.get(node.op_name, default_arrow)
            arrow = arrow_fn(node)
            rows.append(
                f"  {t_offset:7.3f}s  {node.op_name:22s}  "
                f"[{format_ctx(node.ctx)}]"
                f"  {node.duration_ms:6.0f}ms  {arrow}"
            )
            if show_io:
                inputs, outputs = node.exported()
                if node.inputs_from is not None:
                    rows.append(f"              in  (as {node.inputs_from})")
                elif inputs:
                    rows.append(self._format_kv(inputs, prefix="              in  "))
                if outputs:
                    rows.append(self._format_kv(outputs, prefix="              out "))

        summary = self._render_summary(trace)
        return f"{header}\nTIMELINE  (chronological)\n\n" + "\n".join(rows) + f"\n\n{summary}\n"

    # ------------------------------------------------------------------
    # I/O value formatting — compact, collapse-happy
    # ------------------------------------------------------------------

    def _format_kv(self, kv: Dict[str, Any], prefix: str, limit: int = 50) -> str:
        """One indented line: ``prefix key=val key=val …``. Every value
        is passed through :meth:`_format_value` so big data collapses to
        a small placeholder."""
        parts = [f"{k}={self._format_value(v, limit)}" for k, v in kv.items()]
        return prefix + " ".join(parts)

    def _format_value(self, v: Any, limit: int = 50) -> str:
        """Compact display for one value.

        Small scalars inline. Big data (bytes, arrays, nested dicts,
        unserializable handles) collapses to a short tag showing enough
        to identify what it was without dumping the payload.
        """
        if v is None or isinstance(v, bool):
            return str(v)
        if isinstance(v, (int, float)):
            return repr(v)
        if isinstance(v, str):
            return repr(v if len(v) <= limit else f"{v[:limit]}…(+{len(v) - limit})")
        if isinstance(v, dict):
            # media-offload token
            if "$media_ref" in v:
                ref = v.get("$media_ref", "")
                # keep just the leaf filename, drop "media/" prefix
                name = ref.rsplit("/", 1)[-1] if isinstance(ref, str) else "?"
                return f"<media {name[:12]}… {v.get('size', '?')}B>"
            # sanitize marker
            if "$unserializable" in v:
                return f"<{v['$unserializable']}>"
            return f"{{{len(v)} keys}}"
        if isinstance(v, (list, tuple)):
            return f"[{len(v)} items]"
        if isinstance(v, (bytes, bytearray, memoryview)):
            return f"<bytes {len(v)}B>"
        if type(v).__module__ == "numpy" and type(v).__name__ == "ndarray":
            return f"<ndarray {getattr(v, 'shape', '?')} {getattr(v.dtype, 'name', '?')}>"
        return f"<{type(v).__name__}>"

    # ------------------------------------------------------------------
    # View helpers — small enough to inline, isolated for override.
    # ------------------------------------------------------------------

    def _render_header(self, trace: WorkflowTrace) -> str:
        line = "=" * 72
        meta_kv = "  ".join(
            f"{k}={v}" for k, v in trace.metadata.items() if not isinstance(v, (dict, list))
        )
        return (
            f"{line}\n"
            f"{trace.workflow_name}   trace_id={trace.trace_id}   "
            f"dur={trace.duration_ms:.0f}ms   nodes={len(trace.nodes)}\n"
            f"{meta_kv}\n"
            f"{line}\n"
        )

    def _render_summary(self, trace: WorkflowTrace) -> str:
        errors = [n for n in trace.nodes if n.status == "error"]
        return f"{'=' * 72}\nSUMMARY  nodes={len(trace.nodes)}  errors={len(errors)}\n{'=' * 72}"

    def _meta_dict(self, trace: WorkflowTrace) -> Dict[str, Any]:
        return meta_of_trace(trace)

    def _update_latest_symlink(self, root: Path, target: str) -> None:
        """Best-effort ``latest -> <the run's directory>`` symlink.

        Silently skipped when the FS doesn't support symlinks (Windows
        without dev-mode, some FUSE mounts). Failure here isn't fatal —
        `nodes.jsonl` + `view.txt` are already on disk.
        """
        latest = root / "latest"
        try:
            if latest.is_symlink() or latest.exists():
                latest.unlink()
            latest.symlink_to(target)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# ResourceHub config — declare the consumer in ``resources.yaml``
# ---------------------------------------------------------------------------
#
# Example ``resources.yaml``::
#
#     trace_local:
#       default:
#         root: /tmp/operonx_traces
#         media_threshold: 1024
#
# Then access via::
#
#     from operonx.core.registry import ResourceHub
#     consumer = ResourceHub.instance().get("trace_local:default")
#
# Or hand the key to ``Operon(pipeline, trace="trace_local:default")``.


class LocalConsumerConfig(YamlModel):
    """YAML-configurable :class:`LocalConsumer`."""

    _category: ClassVar[str] = "trace_local"

    root: str = ""
    layout: str = "origin"
    media_threshold: int = 1024
    write_view_txt: bool = True
    show_io: bool = True


def _create_local_consumer(cfg: LocalConsumerConfig) -> LocalConsumer:
    return LocalConsumer(
        config={
            "root": cfg.root,
            "layout": cfg.layout,
            "media_threshold": cfg.media_threshold,
            "write_view_txt": cfg.write_view_txt,
            "show_io": cfg.show_io,
        }
    )
