"""03 LLM Chat — Python-side demo.

Three LLM chat graphs. Requires ``OPENAI_API_KEY`` in ``.env``.
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
from examples.python.ex03_llm_chat.workflow import (  # noqa: E402
    build_basic_chat,
    build_chain_chat,
    build_summarize,
)


HERE = Path(__file__).resolve().parent
INPUTS = json.loads((HERE / "inputs.json").read_text(encoding="utf-8"))


SCENARIOS = [
    Scenario(name="basic", build=build_basic_chat, inputs=INPUTS["basic"]),
    Scenario(name="chain", build=build_chain_chat, inputs=INPUTS["chain"]),
    Scenario(name="summarize", build=build_summarize, inputs=INPUTS["summarize"]),
]


async def async_main(runs: int, langfuse: bool) -> None:
    reporter = BenchReporter(example="ex03_llm_chat")
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
    args = parse_args("ex03_llm_chat")
    load_env()
    run_async(async_main(runs=args.runs, langfuse=args.langfuse))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
