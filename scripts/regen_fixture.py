#!/usr/bin/env python3
"""Regenerate a shared spec fixture's `graph.json` + `expected.json`.

Usage:
    uv run python scripts/regen_fixture.py tests/spec/core/ops/parser_json_extract

The fixture dir must already contain:
    builder.py  — defines `build_graph() -> GraphOp`
    inputs.json — engine.run() inputs

Writes:
    graph.json    — scrubbed serialised graph + schema_version
    expected.json — engine.run() output, $state stripped, timing keys stripped
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import sys
from pathlib import Path

TIMING_KEYS = {"$start_time", "$end_time", "$duration_ms"}


def _strip(o):
    if isinstance(o, dict):
        return {k: _strip(v) for k, v in o.items() if k not in TIMING_KEYS}
    if isinstance(o, list):
        return [_strip(v) for v in o]
    return o


def regen(fixture_dir: Path) -> None:
    builder_path = fixture_dir / "builder.py"
    inputs_path = fixture_dir / "inputs.json"
    scratch_path = fixture_dir / "scratch.json"
    graph_path = fixture_dir / "graph.json"
    expected_path = fixture_dir / "expected.json"

    spec = importlib.util.spec_from_file_location(f"_b_{fixture_dir.name}", builder_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    graph = module.build_graph()
    graph.build()

    from operonx.tools.pack import _scrub
    cleaned = _scrub(graph.serialize())
    cleaned["schema_version"] = "1.0"
    graph_path.write_text(json.dumps(cleaned, indent=2, default=str))

    inputs = json.loads(inputs_path.read_text()) if inputs_path.exists() else {}
    scratch = json.loads(scratch_path.read_text()) if scratch_path.exists() else None

    from operonx.core import Operon

    async def _run():
        engine = Operon(graph)
        return await engine.run(inputs=inputs, scratch=scratch)

    result = asyncio.run(_run())
    public = {k: v for k, v in result.items() if k != "$state"}
    expected_path.write_text(json.dumps(_strip(public)))

    print(f"OK  {fixture_dir}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("fixture_dir", type=Path, help="path to fixture dir")
    args = p.parse_args(argv)

    if not (args.fixture_dir / "builder.py").exists():
        print(f"error: {args.fixture_dir} missing builder.py", file=sys.stderr)
        return 1
    if not (args.fixture_dir / "inputs.json").exists():
        print(f"error: {args.fixture_dir} missing inputs.json", file=sys.stderr)
        return 1

    regen(args.fixture_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
