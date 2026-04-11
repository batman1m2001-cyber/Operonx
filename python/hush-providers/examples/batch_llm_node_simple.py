"""Example 2: Test batch mode with LLMOp in a simple graph.

This script tests batch_mode=True with LLMOp using concurrent requests.
Note: batch_mode uses OpenAI Batch API which is slow but 50% cheaper.

Usage:
    cd hush-providers
    uv run python examples/batch_llm_node_simple.py
"""

import asyncio

# Setup path
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


from hush.core import END, PARENT, START, GraphOp
from hush.core.registry import ResourceHub, set_global_hub
from hush.core.states import MemoryState, StateSchema

from hush.providers.ops import LLMOp


async def test_batch_llm_node():
    """Test LLMOp with batch_mode=True using 3 concurrent requests."""
    # Load resources
    config_path = Path(__file__).parent.parent.parent / "resources.yaml"
    hub = ResourceHub.from_yaml(config_path)
    set_global_hub(hub)
    ResourceHub.set_instance(hub)

    print("=" * 60)
    print("TEST: LLMOp with batch_mode=True (3 concurrent requests)")
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
        with GraphOp(name=f"batch_chat_{i}") as workflow:
            llm = LLMOp(
                name="chat",
                resource="gpt-4o",
                batch_mode=True,
                inputs={"messages": PARENT["messages"]},
                outputs={"*": PARENT},
            )
            START >> llm >> END
        workflow.build()

        schema = StateSchema(op=workflow)
        state = MemoryState(schema, inputs={"messages": [{"role": "user", "content": question}]})

        workflows.append(workflow)
        states.append(state)

    print(f"\nSubmitting {len(questions)} requests via LLMOp with batch_mode=True...")
    print("WARNING: OpenAI Batch API takes minutes to process!\n")

    start = time.time()

    # Run all workflows concurrently - BatchCoordinator will batch them together
    await asyncio.gather(*[workflow.run(state) for workflow, state in zip(workflows, states)])

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
    """Test LLMOp without batch mode (normal async) with 3 concurrent requests."""
    # Load resources
    config_path = Path(__file__).parent.parent.parent / "resources.yaml"
    hub = ResourceHub.from_yaml(config_path)
    set_global_hub(hub)
    ResourceHub.set_instance(hub)

    print("=" * 60)
    print("TEST: LLMOp without batch_mode (3 concurrent async requests)")
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
        with GraphOp(name=f"normal_chat_{i}") as workflow:
            llm = LLMOp(
                name="chat",
                resource="gpt-4o",
                batch_mode=False,  # Normal mode
                inputs={"messages": PARENT["messages"]},
                outputs={"*": PARENT},
            )
            START >> llm >> END
        workflow.build()

        schema = StateSchema(op=workflow)
        state = MemoryState(schema, inputs={"messages": [{"role": "user", "content": question}]})

        workflows.append(workflow)
        states.append(state)

    print(f"\nSubmitting {len(questions)} requests via LLMOp (normal async)...\n")

    start = time.time()

    # Run all workflows concurrently
    await asyncio.gather(*[workflow.run(state) for workflow, state in zip(workflows, states)])

    elapsed = time.time() - start

    print(f"\nCompleted in {elapsed:.2f} seconds\n")

    # Print results
    for i, (question, state) in enumerate(zip(questions, states)):
        content = state[f"normal_chat_{i}.chat", "content", None]
        model = state[f"normal_chat_{i}.chat", "model_used", None]
        usage = state[f"normal_chat_{i}.chat", "usage", None]
        print(f"[{i}] Q: {question}")
        print(f"    A: {content}")
        print(f"    Model: {model}")
        print(f"    Usage: {usage}")
        print()


if __name__ == "__main__":
    print("Choose test mode:")
    print("1. Test LLMOp with batch_mode=True (slow, uses OpenAI Batch API)")
    print("2. Test LLMOp without batch_mode (fast, normal async)")
    print()

    choice = input("Enter choice (1 or 2): ").strip()

    if choice == "1":
        asyncio.run(test_batch_llm_node())
    else:
        asyncio.run(test_normal_llm_node())
