"""Tutorial 12: Multi-Model Workflows — Nhiều LLM trong một workflow.

Cần: OPENAI_API_KEY trong .env + llm:gpt-4o, llm:gpt-4o-mini trong resources.yaml

Học được:
- Parallel multi-model: gọi nhiều models song song, so sánh kết quả
- Cost-optimized routing: if_() route simple/complex queries
- Load balancing: ratios phân tải giữa models
- Fallback chain: tự động switch model khi primary fails
- Ensemble + voting: judge model chọn best answer

Chạy: cd hush-tutorial && uv run python examples/12_multi_model.py
"""

import asyncio
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent.parent / ".env")

import os

from hush.core import END, PARENT, START, GraphNode, Hush
from hush.core.nodes import code_node, if_
from hush.providers import llm_, prompt_

# =============================================================================
# Ví dụ 1: Parallel multi-model comparison
# =============================================================================


async def example_1_parallel_models():
    """Gọi 2 models song song và so sánh kết quả."""
    print("=" * 50)
    print("Ví dụ 1: Parallel Multi-Model Comparison")
    print("=" * 50)

    if not os.environ.get("OPENAI_API_KEY"):
        print("  Skipped — OPENAI_API_KEY chưa set")
        return

    @code_node
    def compare(a, b):
        return {
            "gpt4o": a,
            "gpt4o_mini": b,
            "same_length": abs(len(a) - len(b)) < 50,
        }

    with GraphNode(name="multi-model-parallel") as graph:
        p = prompt_(
            template={"system": "Answer in one sentence.", "user": "{query}"},
            query=PARENT["query"],
        )

        # 2 models chạy song song
        gpt4o = llm_(resource_key="gpt-4o", messages=p["messages"])
        gpt4o_mini = llm_(resource_key="gpt-4o-mini", messages=p["messages"])

        # So sánh
        cmp = compare(
            a=gpt4o["content"],
            b=gpt4o_mini["content"],
            outputs={"*": PARENT},
        )

        START >> p >> [gpt4o, gpt4o_mini] >> cmp >> END

    engine = Hush(graph)
    result = await engine.run(inputs={"query": "What is machine learning?"})

    print(f"  GPT-4o:      {result['gpt4o'][:80]}...")
    print(f"  GPT-4o-mini: {result['gpt4o_mini'][:80]}...")


# =============================================================================
# Ví dụ 2: Cost-optimized routing
# =============================================================================


async def example_2_cost_routing():
    """Route simple queries → cheap model, complex → powerful model."""
    print()
    print("=" * 50)
    print("Ví dụ 2: Cost-Optimized Routing")
    print("=" * 50)

    if not os.environ.get("OPENAI_API_KEY"):
        print("  Skipped — OPENAI_API_KEY chưa set")
        return

    with GraphNode(name="smart-routing") as graph:
        # Step 1: Classify complexity (cheap model)
        cls_p = prompt_(
            template={
                "system": "Classify if this query is SIMPLE or COMPLEX. Reply with just one word.",
                "user": "{query}",
            },
            query=PARENT["query"],
        )
        classifier = llm_(
            resource_key="gpt-4o-mini",
            messages=cls_p["messages"],
            outputs={"content": PARENT["classification"]},
        )

        # Step 2: Route
        router = if_(
            PARENT["classification"].apply(lambda c: "SIMPLE" in c.upper()),
            "simple_prompt",
        ).else_("complex_prompt")

        # Simple path — cheap model
        simple_prompt = prompt_(
            template={"system": "Be concise.", "user": "{query}"},
            query=PARENT["query"],
        )
        simple_llm = llm_(
            resource_key="gpt-4o-mini",
            messages=simple_prompt["messages"],
            outputs={"content": PARENT["answer"]},
        )

        # Complex path — powerful model
        complex_prompt = prompt_(
            template={"system": "Think step by step.", "user": "{query}"},
            query=PARENT["query"],
        )
        complex_llm = llm_(
            resource_key="gpt-4o",
            messages=complex_prompt["messages"],
            outputs={"content": PARENT["answer"]},
        )

        START >> cls_p >> classifier >> router
        router >> simple_prompt >> simple_llm
        router >> complex_prompt >> complex_llm
        [simple_llm, complex_llm] >> ~END

    engine = Hush(graph)

    queries = [
        "What is 2 + 2?",
        "Explain the differences between supervised, unsupervised, and reinforcement learning with real-world examples.",
    ]

    for query in queries:
        result = await engine.run(inputs={"query": query})
        cls = result.get("classification", "?")
        answer = result.get("answer", "")
        print(f"\n  Q: {query[:60]}{'...' if len(query) > 60 else ''}")
        print(f"  Classification: {cls}")
        print(f"  Answer: {answer[:100]}{'...' if len(str(answer)) > 100 else ''}")


# =============================================================================
# Ví dụ 3: Load balancing với ratios
# =============================================================================


async def example_3_load_balancing():
    """Phân tải requests giữa models bằng weighted random selection."""
    print()
    print("=" * 50)
    print("Ví dụ 3: Load Balancing")
    print("=" * 50)

    if not os.environ.get("OPENAI_API_KEY"):
        print("  Skipped — OPENAI_API_KEY chưa set")
        return

    with GraphNode(name="load-balanced") as graph:
        p = prompt_(
            template={"system": "Answer briefly.", "user": "{query}"},
            query=PARENT["query"],
        )

        # 70% gpt-4o-mini, 30% gpt-4o
        llm = llm_(
            resource_key=["gpt-4o-mini", "gpt-4o"],
            ratios=[0.7, 0.3],
            messages=p["messages"],
            outputs={"content": PARENT["answer"], "model_used": PARENT["model"]},
        )

        START >> p >> llm >> END

    engine = Hush(graph)

    # Run multiple times to see load balancing
    model_counts = {}
    for i in range(6):
        result = await engine.run(inputs={"query": f"Say hello #{i + 1}"})
        model = result.get("model", "unknown")
        model_counts[model] = model_counts.get(model, 0) + 1

    print("  Distribution across 6 requests:")
    for model, count in model_counts.items():
        print(f"    {model}: {count} requests")
    print("  (Expected ~70% gpt-4o-mini, ~30% gpt-4o)")


# =============================================================================
# Ví dụ 4: Fallback chain
# =============================================================================


async def example_4_fallback():
    """LLMNode fallback — tự động thử model tiếp theo khi primary fails."""
    print()
    print("=" * 50)
    print("Ví dụ 4: Fallback Chain")
    print("=" * 50)

    if not os.environ.get("OPENAI_API_KEY"):
        print("  Skipped — OPENAI_API_KEY chưa set")
        return

    with GraphNode(name="fallback-demo") as graph:
        p = prompt_(
            template={"system": "Answer briefly.", "user": "{query}"},
            query=PARENT["query"],
        )

        # Primary: gpt-4o, fallback: gpt-4o-mini
        llm = llm_(
            resource_key="gpt-4o",
            fallback=["gpt-4o-mini"],
            messages=p["messages"],
            outputs={"content": PARENT["answer"], "model_used": PARENT["model"]},
        )

        START >> p >> llm >> END

    engine = Hush(graph)
    result = await engine.run(inputs={"query": "What is Python?"})
    print(f"  Answer: {result['answer'][:80]}...")
    print(f"  Model used: {result['model']}")
    print("  (If gpt-4o fails → automatically tries gpt-4o-mini)")


# =============================================================================
# Ví dụ 5: Ensemble — judge model chọn best answer
# =============================================================================


async def example_5_ensemble():
    """Gọi 2 models, dùng judge model chọn câu trả lời tốt nhất."""
    print()
    print("=" * 50)
    print("Ví dụ 5: Ensemble with Judge")
    print("=" * 50)

    if not os.environ.get("OPENAI_API_KEY"):
        print("  Skipped — OPENAI_API_KEY chưa set")
        return

    @code_node
    def select(choice, a1, a2):
        return {
            "answer": a1 if "1" in choice else a2,
            "chosen": "gpt-4o" if "1" in choice else "gpt-4o-mini",
        }

    with GraphNode(name="ensemble") as graph:
        p = prompt_(
            template={
                "system": "Answer the question accurately in 1-2 sentences.",
                "user": "{query}",
            },
            query=PARENT["query"],
        )

        # 2 models generate answers in parallel
        model_a = llm_(resource_key="gpt-4o", messages=p["messages"])
        model_b = llm_(resource_key="gpt-4o-mini", messages=p["messages"])

        # Judge picks the best
        jp = prompt_(
            template={
                "system": "Given a question and two answers, reply with just '1' or '2' for the better answer.",
                "user": "Question: {query}\n\nAnswer 1: {a1}\n\nAnswer 2: {a2}",
            },
            query=PARENT["query"],
            a1=model_a["content"],
            a2=model_b["content"],
        )

        judge = llm_(resource_key="gpt-4o-mini", messages=jp["messages"])

        sel = select(
            choice=judge["content"],
            a1=model_a["content"],
            a2=model_b["content"],
            outputs={"*": PARENT},
        )

        START >> p >> [model_a, model_b] >> jp >> judge >> sel >> END

    engine = Hush(graph)
    result = await engine.run(inputs={"query": "What causes the seasons on Earth?"})

    print(f"  Best answer (chosen: {result['chosen']}):")
    print(f"  {result['answer'][:150]}")


# =============================================================================
# Main
# =============================================================================


async def main():
    await example_1_parallel_models()
    await example_2_cost_routing()
    await example_3_load_balancing()
    await example_4_fallback()
    await example_5_ensemble()


if __name__ == "__main__":
    asyncio.run(main())
