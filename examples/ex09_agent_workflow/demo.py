"""09 Agent Workflow — Run locally.

AI Agent with tool-calling: Query → LLM → Execute Tools → Loop → Final Answer.

Requires: OPENAI_API_KEY in .env

Chạy: cd examples && uv run python ex09_agent_workflow/demo.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent.parent / ".env")

from hush.core import Hush

from ex09_agent_workflow.workflow import build_agent

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


async def main():
    if not os.environ.get("OPENAI_API_KEY"):
        print("Skipped — OPENAI_API_KEY chưa set")
        return

    print("=" * 55)
    print("09 Agent Workflow — Tool-calling Agent (@graph.loop)")
    print("=" * 55)

    engine = Hush(build_agent())

    queries = [
        "What is 25 * 4 + 100?",
        "Tell me about Python programming language.",
        "What is 15 * 7, and also tell me about machine learning?",
    ]

    for query in queries:
        result = await engine.run(inputs={"query": query})
        answer = result.get("answer", "No answer")
        print(f"\n  Q: {query}")
        print(f"  A: {str(answer)[:200]}{'...' if len(str(answer)) > 200 else ''}")


if __name__ == "__main__":
    asyncio.run(main())
