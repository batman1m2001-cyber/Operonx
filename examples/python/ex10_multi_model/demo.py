"""10 Multi-Model — Python-side demo.

Parallel comparison, cost routing, load balancing, fallback chains,
ensemble with judge. Requires ``OPENAI_API_KEY``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from examples.python._common import (  # noqa: E402
    BenchReporter,
    Scenario,
    build_engine,
    load_env,
    parse_args,
    run_async,
)
from examples.python.ex10_multi_model.workflow import (  # noqa: E402
    build_cost_routing,
    build_ensemble,
    build_fallback,
    build_load_balanced,
    build_parallel_comparison,
)

HERE = Path(__file__).resolve().parent
INPUTS = json.loads((HERE / "inputs.json").read_text(encoding="utf-8"))


SCENARIOS = [
    Scenario(name="parallel", build=build_parallel_comparison, inputs=INPUTS["parallel"]),
    Scenario(name="routing", build=build_cost_routing, inputs=INPUTS["routing"]),
    Scenario(name="load_balanced", build=build_load_balanced, inputs=INPUTS["load_balanced"]),
    Scenario(name="fallback", build=build_fallback, inputs=INPUTS["fallback"]),
    Scenario(name="ensemble", build=build_ensemble, inputs=INPUTS["ensemble"]),
]


async def async_main(runs: int, langfuse: bool) -> None:
    reporter = BenchReporter(example="ex10_multi_model")
    for sc in SCENARIOS:
        graph = sc.build()
        engine = build_engine(graph, langfuse=langfuse)
        await reporter.record(
            sc.name,
            lambda e=engine, i=sc.inputs: e.run(inputs=i),
            runs=runs,
        )
    reporter.save()


def main() -> int:
    args = parse_args("ex10_multi_model")
    load_env()
    run_async(async_main(runs=args.runs, langfuse=args.langfuse))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
