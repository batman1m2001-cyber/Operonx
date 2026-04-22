"""Parametrized driver for shared spec fixtures.

Each fixture folder under `tests/spec/<area>/<name>/` contains:

- ``graph.json``   — Rust reads this; Python diff-asserts against it
- ``inputs.json``  — inputs passed to the engine
- ``expected.json``— expected outputs (timing keys stripped before compare)
- ``builder.py``   — ``build_graph() -> GraphOp`` — Python-side constructor

The driver discovers every fixture by globbing for ``graph.json``, imports
the adjacent ``builder.py`` via ``importlib``, builds the ``GraphOp``,
runs it through ``Operon``, and compares outputs.

A fixture without a ``builder.py`` is skipped with a clear message — useful
for Rust-only fixtures still awaiting a Python port.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any, Dict

import pytest

from operon.core import Operon

SPEC_ROOT = Path(__file__).parent

TIMING_KEYS = {"$start_time", "$end_time", "$duration_ms", "start_time", "end_time", "duration_ms"}


def _iter_fixtures():
    for graph_path in SPEC_ROOT.rglob("graph.json"):
        yield graph_path.parent


def _strip_timing(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _strip_timing(v) for k, v in obj.items() if k not in TIMING_KEYS}
    if isinstance(obj, list):
        return [_strip_timing(v) for v in obj]
    return obj


def _load_builder(fx: Path):
    builder_path = fx / "builder.py"
    if not builder_path.exists():
        return None
    spec = importlib.util.spec_from_file_location(
        f"spec_builder_{fx.name}", builder_path
    )
    module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


@pytest.mark.parametrize(
    "fx",
    list(_iter_fixtures()),
    ids=lambda p: str(p.relative_to(SPEC_ROOT)).replace("\\", "/"),
)
async def test_fixture(fx: Path):
    builder = _load_builder(fx)
    if builder is None:
        pytest.skip(f"{fx.name}: no builder.py (Rust-only fixture)")

    inputs: Dict[str, Any] = json.loads((fx / "inputs.json").read_text())
    expected: Any = json.loads((fx / "expected.json").read_text())

    graph = builder.build_graph()
    engine = Operon(graph)
    result = await engine.run(inputs=inputs)

    # Python's engine.run() returns `{**outputs, "$state": MemoryState}`;
    # the fixture only describes the user-facing outputs.
    result_public = {k: v for k, v in result.items() if k != "$state"}

    got = _strip_timing(result_public)
    exp = _strip_timing(expected)
    assert got == exp, (
        f"fixture '{fx.relative_to(SPEC_ROOT)}' output mismatch\n"
        f"  got:      {json.dumps(got, indent=2)}\n"
        f"  expected: {json.dumps(exp, indent=2)}"
    )
