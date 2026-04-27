"""13 @graph — Python-side demo.

Modular, reusable workflow components via ``@graph``. No API keys.
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
from examples.python.ex13_graph.workflow import (  # noqa: E402
    build_basic,
    build_chained,
    build_multi_params,
    build_nested,
    build_renamed,
)

HERE = Path(__file__).resolve().parent
INPUTS = json.loads((HERE / "inputs.json").read_text(encoding="utf-8"))


SCENARIOS = [
    Scenario(name="basic", build=build_basic, inputs=INPUTS["basic"]),
    Scenario(name="chained", build=build_chained, inputs=INPUTS["chained"]),
    Scenario(name="renamed", build=build_renamed, inputs=INPUTS["renamed"]),
    Scenario(name="multi_params", build=build_multi_params, inputs=INPUTS["multi_params"]),
    Scenario(name="nested", build=build_nested, inputs=INPUTS["nested"]),
]


async def async_main(runs: int, langfuse: bool) -> None:
    reporter = BenchReporter(example="ex13_graph")
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
    args = parse_args("ex13_graph")
    load_env()
    run_async(async_main(runs=args.runs, langfuse=args.langfuse))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
