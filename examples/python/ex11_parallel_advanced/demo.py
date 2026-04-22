"""11 Parallel Advanced — Python-side demo.

Fan-out/fan-in, generator iteration, partial-failure handling. Pure compute.
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
from examples.python.ex11_parallel_advanced.workflow import (  # noqa: E402
    build_fan_out,
    build_iteration,
    build_partial_failure,
)


HERE = Path(__file__).resolve().parent
INPUTS = json.loads((HERE / "inputs.json").read_text(encoding="utf-8"))


SCENARIOS = [
    Scenario(name="fan_out", build=build_fan_out, inputs=INPUTS["fan_out"]),
    Scenario(name="iteration", build=build_iteration, inputs=INPUTS["iteration"]),
    Scenario(
        name="partial_failure", build=build_partial_failure, inputs=INPUTS["partial_failure"]
    ),
]


async def async_main(runs: int, langfuse: bool) -> None:
    reporter = BenchReporter(example="ex11_parallel_advanced")
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
    args = parse_args("ex11_parallel_advanced")
    load_env()
    run_async(async_main(runs=args.runs, langfuse=args.langfuse))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
