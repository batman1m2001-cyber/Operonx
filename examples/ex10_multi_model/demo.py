"""10 Multi-Model — Run locally.

Demonstrates multiple LLM patterns: parallel comparison, cost routing,
load balancing, fallback chains, and ensemble with judge.

Requires: OPENAI_API_KEY in .env

Chạy: cd examples && uv run python ex10_multi_model/demo.py
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

from ex10_multi_model.workflow import (
    build_cost_routing,
    build_ensemble,
    build_fallback,
    build_load_balanced,
    build_parallel_comparison,
)

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


async def main():
    if not os.environ.get("OPENAI_API_KEY"):
        print("Skipped — OPENAI_API_KEY chưa set")
        return

    # 1. Parallel multi-model comparison
    print("=" * 55)
    print("1. Parallel Multi-Model Comparison")
    print("=" * 55)
    engine = Hush(build_parallel_comparison())
    result = await engine.run(inputs={"query": "What is machine learning?"})
    print(f"  GPT-4o:      {result['gpt4o'][:80]}...")
    print(f"  GPT-4o-mini: {result['gpt4o_mini'][:80]}...")

    # 2. Cost-optimized routing
    print()
    print("=" * 55)
    print("2. Cost-Optimized Routing")
    print("=" * 55)
    engine = Hush(build_cost_routing())
    for query in ["What is 2 + 2?", "Explain supervised vs unsupervised learning with examples."]:
        result = await engine.run(inputs={"query": query})
        cls = result.get("classification", "?")
        answer = result.get("answer", "")
        print(f"\n  Q: {query[:60]}{'...' if len(query) > 60 else ''}")
        print(f"  Classification: {cls}")
        print(f"  Answer: {str(answer)[:100]}{'...' if len(str(answer)) > 100 else ''}")

    # 3. Load balancing
    print()
    print("=" * 55)
    print("3. Load Balancing (70% mini, 30% 4o)")
    print("=" * 55)
    engine = Hush(build_load_balanced())
    model_counts = {}
    for i in range(6):
        result = await engine.run(inputs={"query": f"Say hello #{i + 1}"})
        model = result.get("model", "unknown")
        model_counts[model] = model_counts.get(model, 0) + 1
    for model, count in model_counts.items():
        print(f"  {model}: {count} requests")

    # 4. Fallback chain
    print()
    print("=" * 55)
    print("4. Fallback Chain (gpt-4o -> gpt-4o-mini)")
    print("=" * 55)
    engine = Hush(build_fallback())
    result = await engine.run(inputs={"query": "What is Python?"})
    print(f"  Answer: {result['answer'][:80]}...")
    print(f"  Model used: {result['model']}")

    # 5. Ensemble with judge
    print()
    print("=" * 55)
    print("5. Ensemble with Judge")
    print("=" * 55)
    engine = Hush(build_ensemble())
    result = await engine.run(inputs={"query": "What causes the seasons on Earth?"})
    print(f"  Best answer (chosen: {result['chosen']}):")
    print(f"  {result['answer'][:150]}")


if __name__ == "__main__":
    asyncio.run(main())
