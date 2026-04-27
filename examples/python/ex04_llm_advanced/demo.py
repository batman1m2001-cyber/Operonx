"""04 LLM Advanced — Python-side demo.

Structured output (JSON schema), tool calling, and multi-turn chat.
Requires ``OPENAI_API_KEY`` in ``.env``.
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
from examples.python.ex04_llm_advanced.workflow import (  # noqa: E402
    build_multi_turn,
    build_structured_output,
    build_tool_calling,
)

HERE = Path(__file__).resolve().parent
INPUTS = json.loads((HERE / "inputs.json").read_text(encoding="utf-8"))


SCENARIOS = [
    Scenario(name="structured", build=build_structured_output, inputs=INPUTS["structured"]),
    Scenario(name="tool", build=build_tool_calling, inputs=INPUTS["tool"]),
    Scenario(name="multi_turn", build=build_multi_turn, inputs=INPUTS["multi_turn"]),
]


async def async_main(runs: int, langfuse: bool) -> None:
    reporter = BenchReporter(example="ex04_llm_advanced")
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
    args = parse_args("ex04_llm_advanced")
    load_env()
    run_async(async_main(runs=args.runs, langfuse=args.langfuse))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
