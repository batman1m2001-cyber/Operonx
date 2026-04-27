"""05 Loops & Branches — Python-side demo.

Generator ops (for/map/while) and ``if_`` branch routing.
No API keys required — pure compute.
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
from examples.python.ex05_loops_and_branches.workflow import (  # noqa: E402
    build_branch,
    build_for_loop,
    build_map_op,
    build_while_loop,
)

HERE = Path(__file__).resolve().parent
INPUTS = json.loads((HERE / "inputs.json").read_text(encoding="utf-8"))


SCENARIOS = [
    Scenario(name="for_loop", build=build_for_loop, inputs=INPUTS["for_loop"]),
    Scenario(name="map_op", build=build_map_op, inputs=INPUTS["map_op"]),
    Scenario(name="while_loop", build=build_while_loop, inputs=INPUTS["while_loop"]),
    Scenario(name="branch", build=build_branch, inputs=INPUTS["branch"]),
]


async def async_main(runs: int, langfuse: bool) -> None:
    reporter = BenchReporter(example="ex05_loops_and_branches")
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
    args = parse_args("ex05_loops_and_branches")
    load_env()
    run_async(async_main(runs=args.runs, langfuse=args.langfuse))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
