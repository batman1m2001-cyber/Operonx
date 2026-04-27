"""01 Hello World — Python-side demo.

Three tiny graphs, run through :class:`operonx.core.Operon`. Only the
``engine.run(...)`` call is timed; all authoring / construction work
happens before the reporter's clock starts.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Let this demo run both as ``python -m examples.python.ex01_hello_world.demo``
# and as a plain script (``python examples/python/ex01_hello_world/demo.py``).
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from examples.python._common import (  # noqa: E402
    BenchReporter,
    Scenario,
    build_engine,
    load_env,
    parse_args,
    run_async,
)
from examples.python.ex01_hello_world.workflow import (  # noqa: E402
    build_chain,
    build_hello,
    build_parallel,
)

HERE = Path(__file__).resolve().parent
INPUTS = json.loads((HERE / "inputs.json").read_text(encoding="utf-8"))


SCENARIOS = [
    Scenario(name="hello", build=build_hello, inputs=INPUTS),
    Scenario(name="chain", build=build_chain, inputs={"name": "Operon User"}),
    Scenario(name="parallel", build=build_parallel, inputs={}),
]


async def async_main(runs: int, langfuse: bool) -> None:
    reporter = BenchReporter(example="ex01_hello_world")
    for sc in SCENARIOS:
        # ── Graph + engine construction (NOT timed) ──
        graph = sc.build()
        engine = build_engine(graph, langfuse=langfuse)

        # ── Timed span (warmup + `runs` iterations) ──
        await reporter.record(
            sc.name,
            lambda e=engine, i=sc.inputs: e.run(inputs=i),
            runs=runs,
        )
    reporter.save()


def main() -> int:
    args = parse_args("ex01_hello_world")
    load_env()
    run_async(async_main(runs=args.runs, langfuse=args.langfuse))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
