"""02 Data Pipeline — Python-side demo.

Two tiny pure-compute pipelines (no API keys). Only ``engine.run(...)``
is timed; graph + engine construction happen outside the reporter clock.
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
from examples.python.ex02_data_pipeline.workflow import (  # noqa: E402
    build_data_pipeline,
    build_text_pipeline,
)


HERE = Path(__file__).resolve().parent
INPUTS = json.loads((HERE / "inputs.json").read_text(encoding="utf-8"))


SCENARIOS = [
    Scenario(name="data", build=build_data_pipeline, inputs=INPUTS["data"]),
    Scenario(name="text", build=build_text_pipeline, inputs=INPUTS["text"]),
]


async def async_main(runs: int, langfuse: bool) -> None:
    reporter = BenchReporter(example="ex02_data_pipeline")
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
    args = parse_args("ex02_data_pipeline")
    load_env()
    run_async(async_main(runs=args.runs, langfuse=args.langfuse))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
