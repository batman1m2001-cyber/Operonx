"""Shared helpers for the Python usage-guide demos.

All engine-level work (building the graph, constructing ``Operon``, loading
resources, attaching tracers) happens **outside** the timed span. The
:class:`BenchReporter` here only times ``engine.run(...)`` itself, so
reported latencies reflect pure runtime cost.

Each demo:

1. Builds its graph(s) via ``workflow.py``.
2. Constructs ``Operon(graph, ...)`` — untimed.
3. Calls :meth:`BenchReporter.record` once per scenario for the timed runs
   (with one warmup first).
4. Calls :meth:`BenchReporter.save` to persist the report to
   ``examples/bench_results/<example>_python.json``.

Reports can be compared against the matching Rust-side report offline.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
BENCH_DIR = REPO_ROOT / "examples" / "bench_results"
ENV_FILE = REPO_ROOT / "examples" / ".env"
OPERON_ENV_FILE = REPO_ROOT / ".env"
RESOURCES_FILE = REPO_ROOT / "resources.yaml"


# ── dotenv ──────────────────────────────────────────────────────────────


def load_env() -> None:
    """Best-effort ``.env`` loader. Prefers ``dotenv`` if installed; falls
    back to a tiny parser so demos stay runnable without extra deps."""

    def _naive_load(p: Path) -> None:
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

    for p in (ENV_FILE, OPERON_ENV_FILE):
        if not p.exists():
            continue
        try:
            from dotenv import load_dotenv  # type: ignore

            load_dotenv(p, override=False)
        except ImportError:
            _naive_load(p)


# ── Langfuse attachment (optional) ──────────────────────────────────────


def build_tracer(enable: bool, name_suffix: str = "_python"):
    """Return a :class:`LangfuseTracer` instance when enabled; else ``None``.

    ``name_suffix`` is informational: callers are expected to mutate their
    graph's ``.name`` attribute to include the suffix so Python and Rust
    runs render as separate traces in the Langfuse dashboard.
    """

    if not enable:
        return None
    try:
        from operonx.telemetry.tracers.langfuse import LangfuseTracer

        return LangfuseTracer()
    except Exception as e:  # pragma: no cover - environment-dependent
        print(f"[bench] warn: LangfuseTracer unavailable ({e}); continuing without tracer")
        return None


# ── Scenario + reporter ─────────────────────────────────────────────────


@dataclass
class Scenario:
    """One sub-workflow to time."""

    name: str
    """Short identifier — distinguishes scenarios inside one example."""

    build: Callable[[], Any]
    """Factory returning a fresh ``GraphOp``. Called once per example run."""

    inputs: Dict[str, Any] = field(default_factory=dict)
    """Inputs passed to ``engine.run(inputs=...)``."""

    rename: bool = True
    """When ``True`` (default), the demo appends ``_python`` to the graph's
    name before construction so Langfuse traces don't collide with Rust."""


def _stats(xs: List[float]) -> Dict[str, float]:
    if not xs:
        return {"p50_ms": 0.0, "p95_ms": 0.0, "mean_ms": 0.0}
    xs = sorted(xs)
    n = len(xs)
    p95_idx = max(0, min(n - 1, int(round(0.95 * (n - 1)))))
    return {
        "p50_ms": xs[n // 2],
        "p95_ms": xs[p95_idx],
        "mean_ms": statistics.fmean(xs),
    }


@dataclass
class BenchReporter:
    example: str
    release: bool = True  # informational only — Python has no debug/release split

    def __post_init__(self) -> None:
        BENCH_DIR.mkdir(parents=True, exist_ok=True)
        self.scenarios: Dict[str, Dict[str, Any]] = {}

    async def record(
        self,
        name: str,
        run: Callable[[], Awaitable[Any]],
        *,
        runs: int,
    ) -> Any:
        """Time ``runs`` timed iterations of the coroutine factory ``run``.

        The *warmup* is one untimed call first — everything cached /
        lazy-initialised pays its cost there rather than in the reported
        numbers. Returns the last observed result so callers can print it.
        """

        # Warmup.
        t0 = time.perf_counter()
        result = await run()
        warmup_ms = (time.perf_counter() - t0) * 1000

        latencies: List[float] = []
        for _ in range(runs):
            t0 = time.perf_counter()
            result = await run()
            latencies.append((time.perf_counter() - t0) * 1000)

        stats = _stats(latencies)
        output = _sanitize_output(result)
        self.scenarios[name] = {
            "warmup_ms": warmup_ms,
            "runs_ms": latencies,
            **stats,
            "output": output,
        }
        mean = stats["mean_ms"]
        p50 = stats["p50_ms"]
        p95 = stats["p95_ms"]
        print(
            f"  [{name}] p50={p50:.2f}ms p95={p95:.2f}ms mean={mean:.2f}ms "
            f"warmup={warmup_ms:.2f}ms runs={runs}"
        )
        return result

    def save(self) -> Path:
        path = BENCH_DIR / f"{self.example}_python.json"
        payload = {
            "example": self.example,
            "language": "python",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "scenarios": self.scenarios,
        }
        path.write_text(
            json.dumps(payload, indent=2, default=str, ensure_ascii=False), encoding="utf-8"
        )
        print(f"  → report: {path.relative_to(REPO_ROOT)}")
        return path


def _sanitize_output(result: Any) -> Any:
    """Drop non-JSON-serialisable noise from a run result (e.g. `$state`)."""
    if isinstance(result, dict):
        return {k: v for k, v in result.items() if k != "$state" and _jsonable(v)}
    return result if _jsonable(result) else str(result)


def _jsonable(v: Any) -> bool:
    try:
        json.dumps(v, default=str)
        return True
    except Exception:
        return False


# ── Demo argument parser ────────────────────────────────────────────────


@dataclass
class DemoArgs:
    runs: int
    langfuse: bool


def parse_args(example: str, argv: Optional[List[str]] = None) -> DemoArgs:
    parser = argparse.ArgumentParser(
        prog=f"demo.py ({example} — python)",
        description=(
            "Run the Python side of an Operon example, capturing per-scenario "
            "latency into examples/bench_results/<example>_python.json."
        ),
    )
    parser.add_argument(
        "--runs", type=int, default=5, help="Timed iterations per scenario (default 5)."
    )
    parser.add_argument(
        "--langfuse", action="store_true", help="Attach LangfuseTracer (names suffixed `_python`)."
    )
    args = parser.parse_args(argv)
    return DemoArgs(runs=args.runs, langfuse=args.langfuse)


# ── Engine construction helper ──────────────────────────────────────────


def build_engine(graph, *, langfuse: bool, name_suffix: str = "_python"):
    """Construct ``Operon(graph, ...)`` for a scenario.

    All the initialisation work — schema build, resource hub lookup,
    tracer hookup — happens **here**, before any timing starts.

    The ResourceHub is installed once at module import via
    :func:`_ensure_resources_loaded` so a single bootstrap is shared
    across every scenario.
    """
    import operonx
    from operonx.core import Operon

    _ensure_resources_loaded()

    if name_suffix and hasattr(graph, "name"):
        graph.name = f"{graph.name}{name_suffix}"
    kwargs: Dict[str, Any] = {}
    tracer = build_tracer(langfuse, name_suffix)
    if tracer is not None:
        kwargs["tracer"] = tracer
    _ = operonx  # keep import; bootstrap is reached above
    return Operon(graph, **kwargs)


_RESOURCES_BOOTSTRAPPED = False


def _ensure_resources_loaded() -> None:
    """Bootstrap ``.env`` + ``ResourceHub`` once per process.

    Examples assume the repo's ``resources.yaml`` is the source of
    truth. If it does not exist (e.g. someone copied a single example
    out of the repo), we still call ``bootstrap()`` with ``resources=None``
    so pure-compute scenarios run; provider scenarios will then fail with
    the standard "ResourceHub not initialized" message at op resolution.
    """
    global _RESOURCES_BOOTSTRAPPED
    if _RESOURCES_BOOTSTRAPPED:
        return
    import operonx

    if RESOURCES_FILE.exists():
        operonx.bootstrap(resources=str(RESOURCES_FILE))
    else:
        operonx.bootstrap()
    _RESOURCES_BOOTSTRAPPED = True


# ── Async entry-point wrapper ───────────────────────────────────────────


def run_async(coro: Awaitable[Any]) -> Any:
    """Wrapper around :func:`asyncio.run` so demos don't sprinkle
    ``asyncio.run`` themselves. Also ensures sys.path is set up so
    ``examples.python.<name>.workflow`` imports work from anywhere."""
    sys.path.insert(0, str(REPO_ROOT))
    return asyncio.run(coro)
