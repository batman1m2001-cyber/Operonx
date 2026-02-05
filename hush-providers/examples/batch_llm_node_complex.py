"""Example 3: Complex graph with multiple LLMNodes mixing batch and non-batch.

This script demonstrates:
- Multiple LLMNodes in one graph
- Mixing batch_mode=True and batch_mode=False nodes
- Load balancing with multiple models
- Fallback configuration

Usage:
    cd hush-providers
    uv run python examples/batch_llm_node_complex.py
"""

import asyncio
import logging
import time
from pathlib import Path

# Setup path
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from hush.core import GraphNode, START, END, PARENT
from hush.core.registry import ResourceHub, set_global_hub
from hush.core.states import StateSchema, MemoryState
from hush.providers.registry import LLMPlugin
from hush.providers.nodes import LLMNode, PromptNode


async def test_mixed_batch_async():
    """Test graph with mixed batch and async LLMNodes."""
    # Load resources
    config_path = Path(__file__).parent.parent.parent / "resources.yaml"
    hub = ResourceHub.from_yaml(config_path)
    set_global_hub(hub)
    ResourceHub.set_instance(hub)

    print("=" * 60)
    print("TEST: Mixed batch and async LLMNodes")
    print("=" * 60)

    # Graph structure:
    # START -> prompt -> [async_llm, batch_llm] -> summarizer -> END
    #
    # async_llm: fast, normal async call
    # batch_llm: uses OpenAI Batch API (slow but cheaper)
    # summarizer: combines results

    with GraphNode(name="mixed_workflow") as workflow:
        # Prompt node to format input
        prompt = PromptNode(
            name="prompt",
            inputs={
                "system_prompt": "You are a helpful assistant.",
                "user_prompt": "Answer this question: {question}",
                "question": PARENT["question"]
            },
            outputs={"messages": PARENT["messages"]}
        )

        # Fast async LLM (normal mode)
        async_llm = LLMNode(
            name="fast_llm",
            resource_key="gpt-4o",
            batch_mode=False,
            inputs={"messages": PARENT["messages"]},
            outputs={
                "content": PARENT["fast_response"],
                "tokens_used": PARENT["fast_tokens"]
            }
        )

        # Batch LLM (uses Batch API - slow but 50% cheaper)
        # In real use case, you'd use this for non-time-sensitive tasks
        batch_llm = LLMNode(
            name="batch_llm",
            resource_key="gpt-4o",
            batch_mode=True,
            inputs={"messages": PARENT["messages"]},
            outputs={
                "content": PARENT["batch_response"],
                "tokens_used": PARENT["batch_tokens"]
            }
        )

        # Connect nodes
        START >> prompt
        prompt >> async_llm >> END
        # Note: batch_llm would run in parallel if enabled
        prompt >> batch_llm >> END

    workflow.build()

    # Create state
    schema = StateSchema(node=workflow)
    state = MemoryState(schema, inputs={
        "question": "What is the capital of France? Answer in one word."
    })

    print(f"\nRunning workflow with fast async LLM...\n")

    start = time.time()
    result = await workflow.run(state)
    elapsed = time.time() - start

    print(f"Completed in {elapsed:.2f} seconds")
    print(f"Fast response: {state['mixed_workflow', 'fast_response', None]}")
    print(f"Fast tokens: {state['mixed_workflow', 'fast_tokens', None]}")


async def test_load_balancing_with_fallback():
    """Test LLMNode with load balancing and fallback."""
    config_path = Path(__file__).parent.parent.parent / "resources.yaml"
    hub = ResourceHub.from_yaml(config_path)
    set_global_hub(hub)
    ResourceHub.set_instance(hub)

    print("=" * 60)
    print("TEST: Load balancing with fallback")
    print("=" * 60)

    with GraphNode(name="lb_workflow") as workflow:
        llm = LLMNode(
            name="chat",
            # Load balance between gpt-4o (70%) and claude (30%)
            resource_key=["gpt-4o", "or-claude-4-sonnet"],
            ratios=[0.7, 0.3],
            # Fallback to the other model if primary fails
            fallback=["or-claude-4-sonnet", "gpt-4o"],
            inputs={"messages": PARENT["messages"]},
            outputs={"*": PARENT}
        )
        START >> llm >> END

    workflow.build()

    # Run multiple times to see load balancing
    print("\nRunning 5 requests with load balancing...\n")

    for i in range(5):
        schema = StateSchema(node=workflow)
        state = MemoryState(schema, inputs={
            "messages": [{"role": "user", "content": f"Say 'test {i}' in one word"}]
        })

        start = time.time()
        await workflow.run(state)
        elapsed = time.time() - start

        model = state["lb_workflow.chat", "model_used", None]
        content = state["lb_workflow.chat", "content", None]
        print(f"[{i}] Model: {model:25s} | Response: {content:15s} | Time: {elapsed:.2f}s")


async def test_parallel_async_execution():
    """Test parallel async execution (fast alternative to batch API)."""
    config_path = Path(__file__).parent.parent.parent / "resources.yaml"
    hub = ResourceHub.from_yaml(config_path)
    set_global_hub(hub)
    ResourceHub.set_instance(hub)

    print("=" * 60)
    print("TEST: Parallel async execution (fast)")
    print("=" * 60)
    print("This is the FAST way to run multiple LLM calls concurrently.")
    print("Use this instead of OpenAI Batch API for real-time use cases.\n")

    # Create multiple workflows
    workflows = []
    states = []

    questions = [
        "What is 2+2? Answer with just the number.",
        "What is the capital of Japan? Answer in one word.",
        "What color is the sky? Answer in one word.",
        "How many days in a week? Answer with just the number.",
        "What is the largest planet? Answer in one word.",
    ]

    for i, question in enumerate(questions):
        with GraphNode(name=f"workflow_{i}") as workflow:
            llm = LLMNode(
                name="chat",
                resource_key="gpt-4o",
                batch_mode=False,  # Fast async mode
                inputs={"messages": PARENT["messages"]},
                outputs={"*": PARENT}
            )
            START >> llm >> END
        workflow.build()

        schema = StateSchema(node=workflow)
        state = MemoryState(schema, inputs={
            "messages": [{"role": "user", "content": question}]
        })

        workflows.append(workflow)
        states.append(state)

    print(f"Running {len(workflows)} LLM calls in parallel...\n")

    start = time.time()

    # Run all workflows concurrently
    await asyncio.gather(*[
        workflow.run(state)
        for workflow, state in zip(workflows, states)
    ])

    elapsed = time.time() - start

    print(f"All {len(workflows)} calls completed in {elapsed:.2f} seconds\n")

    for i, (question, state) in enumerate(zip(questions, states)):
        content = state[f"workflow_{i}.chat", "content", None]
        print(f"[{i}] Q: {question[:40]:40s} | A: {content}")


if __name__ == "__main__":
    print("Choose test mode:")
    print("1. Mixed batch and async LLMNodes")
    print("2. Load balancing with fallback")
    print("3. Parallel async execution (FAST - recommended)")
    print()

    choice = input("Enter choice (1, 2, or 3): ").strip()

    if choice == "1":
        asyncio.run(test_mixed_batch_async())
    elif choice == "2":
        asyncio.run(test_load_balancing_with_fallback())
    else:
        asyncio.run(test_parallel_async_execution())
