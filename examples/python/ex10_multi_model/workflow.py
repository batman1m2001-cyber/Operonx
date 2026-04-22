"""Shared workflow definitions for ex10_multi_model.

Multi-model patterns: parallel comparison, cost routing, load balancing,
fallback chains, ensemble with judge.
Requires ``OPENAI_API_KEY``.
"""

from operon.core import END, PARENT, START, GraphOp, op
from operon.core.ops.flow.branch_op import if_
from operon.providers import LLMOp, PromptOp

# =============================================================================
# Ops
# =============================================================================


@op
def is_simple(classification: str):
    """Check if classification indicates SIMPLE."""
    return {"is_simple": "SIMPLE" in classification.upper()}


@op
def compare(a, b):
    """Compare outputs from two models."""
    return {
        "gpt4o": a,
        "gpt4o_mini": b,
        "same_length": abs(len(a) - len(b)) < 50,
    }


@op
def select(choice, a1, a2):
    """Select best answer based on judge's choice."""
    return {
        "answer": a1 if "1" in choice else a2,
        "chosen": "gpt-4o" if "1" in choice else "gpt-4o-mini",
    }


# =============================================================================
# Graph builders
# =============================================================================


def build_parallel_comparison() -> GraphOp:
    """Graph: prompt → [gpt-4o, gpt-4o-mini] parallel → compare."""
    with GraphOp(name="multi-model-parallel") as g:
        p = PromptOp.of(
            template={"system": "Answer in one sentence.", "user": "{query}"},
            query=PARENT["query"],
        )
        gpt4o = LLMOp.of(resource="gpt-4o", messages=p["messages"])
        gpt4o_mini = LLMOp.of(resource="gpt-4o-mini", messages=p["messages"])
        cmp = compare(a=gpt4o["content"], b=gpt4o_mini["content"])
        START >> p >> [gpt4o, gpt4o_mini] >> cmp >> END
    return g


def build_cost_routing() -> GraphOp:
    """Graph: classify complexity → route simple/complex to different models."""
    with GraphOp(name="smart-routing") as g:
        cls_p = PromptOp.of(
            template={
                "system": "Classify if this query is SIMPLE or COMPLEX. Reply with just one word.",
                "user": "{query}",
            },
            query=PARENT["query"],
        )
        classifier = LLMOp.of(
            resource="gpt-4o-mini",
            messages=cls_p["messages"],
            outputs={"content": PARENT["classification"]},
        )

        check = is_simple(classification=PARENT["classification"])

        router = if_(
            check["is_simple"],
            "simple_prompt",
        ).else_("complex_prompt")

        simple_prompt = PromptOp.of(
            template={"system": "Be concise.", "user": "{query}"},
            query=PARENT["query"],
        )
        simple_llm = LLMOp.of(
            resource="gpt-4o-mini",
            messages=simple_prompt["messages"],
            outputs={"content": PARENT["answer"]},
        )

        complex_prompt = PromptOp.of(
            template={"system": "Think step by step.", "user": "{query}"},
            query=PARENT["query"],
        )
        complex_llm = LLMOp.of(
            resource="gpt-4o",
            messages=complex_prompt["messages"],
            outputs={"content": PARENT["answer"]},
        )

        START >> cls_p >> classifier >> check >> router
        router >> simple_prompt >> simple_llm
        router >> complex_prompt >> complex_llm
        [simple_llm, complex_llm] >> ~END
    return g


def build_load_balanced() -> GraphOp:
    """Graph: prompt → LLM with weighted model selection."""
    with GraphOp(name="load-balanced") as g:
        p = PromptOp.of(
            template={"system": "Answer briefly.", "user": "{query}"},
            query=PARENT["query"],
        )
        llm = LLMOp.of(
            resource=["gpt-4o-mini", "gpt-4o"],
            ratios=[0.7, 0.3],
            messages=p["messages"],
            outputs={"content": PARENT["answer"], "model_used": PARENT["model"]},
        )
        START >> p >> llm >> END
    return g


def build_fallback() -> GraphOp:
    """Graph: prompt → LLM with fallback chain (gpt-4o → gpt-4o-mini)."""
    with GraphOp(name="fallback-demo") as g:
        p = PromptOp.of(
            template={"system": "Answer briefly.", "user": "{query}"},
            query=PARENT["query"],
        )
        llm = LLMOp.of(
            resource="gpt-4o",
            fallback=["gpt-4o-mini"],
            messages=p["messages"],
            outputs={"content": PARENT["answer"], "model_used": PARENT["model"]},
        )
        START >> p >> llm >> END
    return g


def build_ensemble() -> GraphOp:
    """Graph: 2 models parallel → judge picks best answer."""
    with GraphOp(name="ensemble") as g:
        p = PromptOp.of(
            template={
                "system": "Answer the question accurately in 1-2 sentences.",
                "user": "{query}",
            },
            query=PARENT["query"],
        )
        model_a = LLMOp.of(resource="gpt-4o", messages=p["messages"])
        model_b = LLMOp.of(resource="gpt-4o-mini", messages=p["messages"])

        jp = PromptOp.of(
            template={
                "system": "Given a question and two answers, reply with just '1' or '2' for the better answer.",
                "user": "Question: {query}\n\nAnswer 1: {a1}\n\nAnswer 2: {a2}",
            },
            query=PARENT["query"],
            a1=model_a["content"],
            a2=model_b["content"],
        )
        judge = LLMOp.of(resource="gpt-4o-mini", messages=jp["messages"])

        sel = select(
            choice=judge["content"],
            a1=model_a["content"],
            a2=model_b["content"],
            outputs={"*": PARENT},
        )
        START >> p >> [model_a, model_b] >> jp >> judge >> sel >> END
    return g
