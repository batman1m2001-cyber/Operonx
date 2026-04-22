"""14 Streaming & Tracing — Python-side demo.

Generator pipelines exercised via ``engine.run(...)``. No API keys required.
Streaming via ``engine.start(...)`` is exercised in the original Hush tutorial
but not timed here; the reporter captures the accumulated ``engine.run(...)``
result.
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
from examples.python.ex14_streaming_tracing.workflow import (  # noqa: E402
    build_async_pipeline,
    build_text_pipeline,
)


HERE = Path(__file__).resolve().parent
INPUTS = json.loads((HERE / "inputs.json").read_text(encoding="utf-8"))


SCENARIOS = [
    Scenario(name="text", build=build_text_pipeline, inputs=INPUTS["text"]),
    Scenario(name="async_counter", build=build_async_pipeline, inputs=INPUTS["async_counter"]),
]


async def async_main(runs: int, langfuse: bool) -> None:
    reporter = BenchReporter(example="ex14_streaming_tracing")
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
    args = parse_args("ex14_streaming_tracing")
    load_env()
    run_async(async_main(runs=args.runs, langfuse=args.langfuse))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
