"""Example 2: Test batch mode with LLMNode in a simple graph.

This script tests batch_mode=True with LLMNode using concurrent requests.
Note: batch_mode uses OpenAI Batch API which is slow but 50% cheaper.

Usage:
    cd hush-providers
    uv run python examples/batch_llm_node_simple.py
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
from hush.providers.nodes import LLMNode


async def test_batch_llm_node():
    """Test LLMNode with batch_mode=True using 3 concurrent requests."""
    # Load resources
    config_path = Path(__file__).parent.parent.parent / "resources.yaml"
    hub = ResourceHub.from_yaml(config_path)
    set_global_hub(hub)
    ResourceHub.set_instance(hub)

    print("=" * 60)
    print("TEST: LLMNode with batch_mode=True (3 concurrent requests)")
    print("=" * 60)

    # Questions for batch processing
    questions = [
        "Say 'hello' in one word",
        "What is 2+2? Answer with just the number.",
        "What color is the sky? Answer in one word.",
    ]

    # Create workflows and states for concurrent execution
    workflows = []
    states = []

    for i, question in enumerate(questions):
        with GraphNode(name=f"batch_chat_{i}") as workflow:
            llm = LLMNode(
                name="chat",
                resource_key="gpt-4o",
                batch_mode=True,
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

    print(f"\nSubmitting {len(questions)} requests via LLMNode with batch_mode=True...")
    print("WARNING: OpenAI Batch API takes minutes to process!\n")

    start = time.time()

    # Run all workflows concurrently - BatchCoordinator will batch them together
    await asyncio.gather(*[
        workflow.run(state)
        for workflow, state in zip(workflows, states)
    ])

    elapsed = time.time() - start

    print(f"\nCompleted in {elapsed:.1f} seconds\n")

    # Print results
    for i, (question, state) in enumerate(zip(questions, states)):
        content = state[f"batch_chat_{i}.chat", "content", None]
        model = state[f"batch_chat_{i}.chat", "model_used", None]
        print(f"[{i}] Q: {question}")
        print(f"    A: {content}")
        print(f"    Model: {model}")
        print()


async def test_normal_llm_node():
    """Test LLMNode without batch mode (normal async) with 3 concurrent requests."""
    # Load resources
    config_path = Path(__file__).parent.parent.parent / "resources.yaml"
    hub = ResourceHub.from_yaml(config_path)
    set_global_hub(hub)
    ResourceHub.set_instance(hub)

    print("=" * 60)
    print("TEST: LLMNode without batch_mode (3 concurrent async requests)")
    print("=" * 60)

    # Questions for concurrent processing
    questions = [
        "Say 'hello' in one word",
        "What is 2+2? Answer with just the number.",
        "What color is the sky? Answer in one word.",
    ]

    # Create workflows and states for concurrent execution
    workflows = []
    states = []

    for i, question in enumerate(questions):
        with GraphNode(name=f"normal_chat_{i}") as workflow:
            llm = LLMNode(
                name="chat",
                resource_key="gpt-4o",
                batch_mode=False,  # Normal mode
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

    print(f"\nSubmitting {len(questions)} requests via LLMNode (normal async)...\n")

    start = time.time()

    # Run all workflows concurrently
    await asyncio.gather(*[
        workflow.run(state)
        for workflow, state in zip(workflows, states)
    ])

    elapsed = time.time() - start

    print(f"\nCompleted in {elapsed:.2f} seconds\n")

    # Print results
    for i, (question, state) in enumerate(zip(questions, states)):
        content = state[f"normal_chat_{i}.chat", "content", None]
        model = state[f"normal_chat_{i}.chat", "model_used", None]
        tokens = state[f"normal_chat_{i}.chat", "tokens_used", None]
        print(f"[{i}] Q: {question}")
        print(f"    A: {content}")
        print(f"    Model: {model}")
        print(f"    Tokens: {tokens}")
        print()


if __name__ == "__main__":
    print("Choose test mode:")
    print("1. Test LLMNode with batch_mode=True (slow, uses OpenAI Batch API)")
    print("2. Test LLMNode without batch_mode (fast, normal async)")
    print()

    choice = input("Enter choice (1 or 2): ").strip()

    if choice == "1":
        asyncio.run(test_batch_llm_node())
    else:
        asyncio.run(test_normal_llm_node())
