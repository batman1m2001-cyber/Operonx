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
from operonx.providers import LLMOp, PromptOp


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
    """One prompt → both gpt-4o and gpt-4o-mini → compare lengths."""
    p = PromptOp.of(
        template={"system": "Answer in one sentence.", "user": "{query}"},
        query=query,
    )
    gpt4o = LLMOp.of(resource="gpt-4o", messages=p["messages"])
    gpt4o_mini = LLMOp.of(resource="gpt-4o-mini", messages=p["messages"])
    cmp = compare(a=gpt4o["content"], b=gpt4o_mini["content"])
    START >> p >> [gpt4o, gpt4o_mini] >> cmp >> END


@graph
def cost_routing(query):
    """Classify SIMPLE/COMPLEX, route cheap/expensive accordingly."""
    cls_p = PromptOp.of(
        template={
            "system": "Classify if this query is SIMPLE or COMPLEX. Reply with just one word.",
            "user": "{query}",
        },
        query=query,
    )
    classifier = LLMOp.of(resource="gpt-4o-mini", messages=cls_p["messages"])
    check = is_simple(classification=classifier["content"])
    router = if_(check["is_simple"], "simple_p").else_("complex_p")

    simple_p = PromptOp.of(
        template={"system": "Be concise.", "user": "{query}"},
        query=query,
    )
    simple_llm = LLMOp.of(resource="gpt-4o-mini", messages=simple_p["messages"])
    complex_p = PromptOp.of(
        template={"system": "Think step by step.", "user": "{query}"},
        query=query,
    )
    complex_llm = LLMOp.of(resource="gpt-4o", messages=complex_p["messages"])

    simple_llm["content"] >> PARENT["answer"]
    complex_llm["content"] >> PARENT["answer"]

    START >> cls_p >> classifier >> check >> router
    router >> simple_p >> simple_llm
    router >> complex_p >> complex_llm
    [simple_llm, complex_llm] >> ~END


@graph
def load_balanced(query):
    """Weighted model selection — 70% gpt-4o-mini, 30% gpt-4o."""
    p = PromptOp.of(
        template={"system": "Answer briefly.", "user": "{query}"},
        query=query,
    )
    llm = LLMOp.of(
        resource=["gpt-4o-mini", "gpt-4o"],
        ratios=[0.7, 0.3],
        messages=p["messages"],
    )
    START >> p >> llm >> END


@graph
def fallback_chain(query):
    """gpt-4o with gpt-4o-mini as fallback."""
    p = PromptOp.of(
        template={"system": "Answer briefly.", "user": "{query}"},
        query=query,
    )
    llm = LLMOp.of(
        resource="gpt-4o",
        fallback=["gpt-4o-mini"],
        messages=p["messages"],
    )
    START >> p >> llm >> END


@graph
def ensemble(query):
    """Two models in parallel; a judge LLM picks the better answer."""
    p = PromptOp.of(
        template={"system": "Answer the question accurately in 1-2 sentences.", "user": "{query}"},
        query=query,
    )
    model_a = LLMOp.of(resource="gpt-4o", messages=p["messages"])
    model_b = LLMOp.of(resource="gpt-4o-mini", messages=p["messages"])

    jp = PromptOp.of(
        template={
            "system": "Given a question and two answers, reply with just '1' or '2' for the better answer.",
            "user": "Question: {query}\n\nAnswer 1: {a1}\n\nAnswer 2: {a2}",
        },
        query=query,
        a1=model_a["content"],
        a2=model_b["content"],
    )
    judge = LLMOp.of(resource="gpt-4o-mini", messages=jp["messages"])
    sel = select(choice=judge["content"], a1=model_a["content"], a2=model_b["content"])
    START >> p >> [model_a, model_b] >> jp >> judge >> sel >> END


async def main() -> None:
    operonx.bootstrap()

    runs = [
        ("parallel", parallel_comparison(query=PARENT["query"]),
            {"query": "What is machine learning?"}),
        ("routing", cost_routing(query=PARENT["query"]),
            {"query": "Explain supervised vs unsupervised learning with examples."}),
        ("load_balanced", load_balanced(query=PARENT["query"]),
            {"query": "Say hello #1"}),
        ("fallback", fallback_chain(query=PARENT["query"]),
            {"query": "What is Python?"}),
        ("ensemble", ensemble(query=PARENT["query"]),
            {"query": "What causes the seasons on Earth?"}),
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
