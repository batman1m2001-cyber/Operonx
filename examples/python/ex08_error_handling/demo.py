"""08 Error Handling — Python-side demo.

Error capture, `if_` routing, retry + fallback, LLM fallback chain.
Scenarios 1-3 are pure compute; scenario 4 needs ``OPENAI_API_KEY``.
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
from examples.python.ex08_error_handling.workflow import (  # noqa: E402
    build_error_capture,
    build_error_routing,
    build_llm_fallback,
    build_retry_fallback,
)


HERE = Path(__file__).resolve().parent
INPUTS = json.loads((HERE / "inputs.json").read_text(encoding="utf-8"))


SCENARIOS = [
    Scenario(name="capture", build=build_error_capture, inputs=INPUTS["capture"]),
    Scenario(name="routing", build=build_error_routing, inputs=INPUTS["routing"]),
    Scenario(name="retry", build=build_retry_fallback, inputs=INPUTS["retry"]),
    Scenario(name="llm_fallback", build=build_llm_fallback, inputs=INPUTS["llm_fallback"]),
]


async def async_main(runs: int, langfuse: bool) -> None:
    reporter = BenchReporter(example="ex08_error_handling")
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
    args = parse_args("ex08_error_handling")
    load_env()
    run_async(async_main(runs=args.runs, langfuse=args.langfuse))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
