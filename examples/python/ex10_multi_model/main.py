"""10 Multi-Model — parallel comparison, cost routing, load balancing,
fallback chain, ensemble with judge.

Requires ``OPENAI_API_KEY`` and ``llm:gpt-4o`` + ``llm:gpt-4o-mini`` in
``resources.yaml``. Run from this directory:

    uv sync
    cp .env.example .env
    uv run python main.py
"""

from __future__ import annotations

import asyncio

import operonx
from operonx.core import END, PARENT, START, Operon, graph, op
from operonx.core.ops.flow.branch_op import if_
from operonx.providers import LLMOp


@op
def is_simple(classification: str):
    return {"is_simple": "SIMPLE" in classification.upper()}


@op
def compare(a, b):
    return {"gpt4o": a, "gpt4o_mini": b, "same_length": abs(len(a) - len(b)) < 50}


@op
def select(choice, a1, a2):
    return {
        "answer": a1 if "1" in choice else a2,
        "chosen": "gpt-4o" if "1" in choice else "gpt-4o-mini",
    }


@graph
def parallel_comparison(query):
    """Fan out to both gpt-4o and gpt-4o-mini in parallel → compare lengths."""
    gpt4o = LLMOp.of(
        resource="gpt-4o",
        prompt={"system": "Answer in one sentence.", "user": "{query}"},
        query=query,
    )
    gpt4o_mini = LLMOp.of(
        resource="gpt-4o-mini",
        prompt={"system": "Answer in one sentence.", "user": "{query}"},
        query=query,
    )
    cmp = compare(a=gpt4o["content"], b=gpt4o_mini["content"])
    START >> [gpt4o, gpt4o_mini] >> cmp >> END


@graph
def cost_routing(query):
    """Classify SIMPLE/COMPLEX, route cheap/expensive accordingly."""
    classifier = LLMOp.of(
        resource="gpt-4o-mini",
        prompt={
            "system": "Classify if this query is SIMPLE or COMPLEX. Reply with just one word.",
            "user": "{query}",
        },
        query=query,
    )
    check = is_simple(classification=classifier["content"])
    router = if_(check["is_simple"], "simple_llm").else_("complex_llm")

    simple_llm = LLMOp.of(
        resource="gpt-4o-mini",
        prompt={"system": "Be concise.", "user": "{query}"},
        query=query,
    )
    complex_llm = LLMOp.of(
        resource="gpt-4o",
        prompt={"system": "Think step by step.", "user": "{query}"},
        query=query,
    )

    simple_llm["content"] >> PARENT["answer"]
    complex_llm["content"] >> PARENT["answer"]

    START >> classifier >> check >> router
    router >> [simple_llm, complex_llm] >> END


@graph
def load_balanced(query):
    """Weighted model selection — 70% gpt-4o-mini, 30% gpt-4o."""
    llm = LLMOp.of(
        resource=["gpt-4o-mini", "gpt-4o"],
        ratios=[0.7, 0.3],
        prompt={"system": "Answer briefly.", "user": "{query}"},
        query=query,
    )
    START >> llm >> END


@graph
def fallback_chain(query):
    """gpt-4o with gpt-4o-mini as fallback."""
    llm = LLMOp.of(
        resource="gpt-4o",
        fallback=["gpt-4o-mini"],
        prompt={"system": "Answer briefly.", "user": "{query}"},
        query=query,
    )
    START >> llm >> END


@graph
def ensemble(query):
    """Two models in parallel; a judge LLM picks the better answer."""
    model_a = LLMOp.of(
        resource="gpt-4o",
        prompt={"system": "Answer the question accurately in 1-2 sentences.", "user": "{query}"},
        query=query,
    )
    model_b = LLMOp.of(
        resource="gpt-4o-mini",
        prompt={"system": "Answer the question accurately in 1-2 sentences.", "user": "{query}"},
        query=query,
    )

    judge = LLMOp.of(
        resource="gpt-4o-mini",
        prompt={
            "system": "Given a question and two answers, reply with just '1' or '2' for the better answer.",
            "user": "Question: {query}\n\nAnswer 1: {a1}\n\nAnswer 2: {a2}",
        },
        query=query,
        a1=model_a["content"],
        a2=model_b["content"],
    )
    sel = select(choice=judge["content"], a1=model_a["content"], a2=model_b["content"])
    START >> [model_a, model_b] >> judge >> sel >> END


async def main() -> None:
    operonx.bootstrap()

    runs = [
        (
            "parallel",
            parallel_comparison(query=PARENT["query"]),
            {"query": "What is machine learning?"},
        ),
        (
            "routing",
            cost_routing(query=PARENT["query"]),
            {"query": "Explain supervised vs unsupervised learning with examples."},
        ),
        ("load_balanced", load_balanced(query=PARENT["query"]), {"query": "Say hello #1"}),
        ("fallback", fallback_chain(query=PARENT["query"]), {"query": "What is Python?"}),
        (
            "ensemble",
            ensemble(query=PARENT["query"]),
            {"query": "What causes the seasons on Earth?"},
        ),
    ]
    for label, g, inputs in runs:
        try:
            result = await Operon(g).run(inputs=inputs)
            content = {k: v for k, v in result.items() if k != "$state"}
            print(f"[{label}] {content}")
        except Exception as e:
            print(f"[{label}] error: {e!r}")


if __name__ == "__main__":
    asyncio.run(main())
