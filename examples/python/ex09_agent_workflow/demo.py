"""09 Agent Workflow — Python-side demo.

Tool-calling agent built on ``@graph.loop``. Requires ``OPENAI_API_KEY``.
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
from examples.python.ex09_agent_workflow.workflow import build_agent  # noqa: E402

HERE = Path(__file__).resolve().parent
INPUTS = json.loads((HERE / "inputs.json").read_text(encoding="utf-8"))


SCENARIOS = [
    Scenario(name="calc", build=build_agent, inputs=INPUTS["calc"]),
    Scenario(name="search", build=build_agent, inputs=INPUTS["search"]),
    Scenario(name="combined", build=build_agent, inputs=INPUTS["combined"]),
]


async def async_main(runs: int, langfuse: bool) -> None:
    reporter = BenchReporter(example="ex09_agent_workflow")
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
    args = parse_args("ex09_agent_workflow")
    load_env()
    run_async(async_main(runs=args.runs, langfuse=args.langfuse))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
