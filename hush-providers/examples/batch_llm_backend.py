"""Example 1: Test batch mode with LLM backend directly.

This script tests the OpenAI Batch API through the LLM backend.
Note: OpenAI Batch API takes minutes to hours to process.

Usage:
    cd hush-providers
    uv run python examples/batch_llm_backend.py
"""

import asyncio
import time
from pathlib import Path

# Setup path
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from hush.core.registry import ResourceHub, set_global_hub
from hush.providers.registry import LLMPlugin


async def test_batch_backend():
    """Test batch mode directly with LLM backend."""
    # Load resources
    config_path = Path(__file__).parent.parent.parent / "resources.yaml"
    hub = ResourceHub.from_yaml(config_path)
    set_global_hub(hub)
    ResourceHub.set_instance(hub)

    llm = hub.llm("gpt-4o")
    print(f"Testing batch mode with model: {llm.config.model}")

    # Prepare batch messages
    batch_messages = [
        [{"role": "user", "content": "Say 'hello' in one word"}],
        [{"role": "user", "content": "Say 'goodbye' in one word"}],
        [{"role": "user", "content": "What is 2+2? Answer with just the number."}],
    ]

    print(f"\nSubmitting {len(batch_messages)} requests to OpenAI Batch API...")
    print("WARNING: OpenAI Batch API takes minutes to hours to process!")
    print("For fast concurrent execution, use asyncio.gather instead.\n")

    start_time = time.time()

    try:
        results = await llm.generate_batch(
            batch_messages=batch_messages,
            temperature=0.0,
            poll_interval=5.0,  # Check every 5 seconds
            timeout=600.0  # 10 minute timeout
        )

        elapsed = time.time() - start_time
        print(f"\nBatch completed in {elapsed:.1f} seconds")
        print(f"Got {len(results)} results:\n")

        for i, result in enumerate(results):
            print(f"[{i}] Content: {result.choices[0].message.content}")
            if result.usage:
                print(f"    Tokens: prompt={result.usage.prompt_tokens}, "
                      f"completion={result.usage.completion_tokens}")
            print()

    except Exception as e:
        print(f"Error: {e}")

    finally:
        await llm.close()


async def test_concurrent_comparison():
    """Compare batch API vs concurrent async execution."""
    config_path = Path(__file__).parent.parent.parent / "resources.yaml"
    hub = ResourceHub.from_yaml(config_path)
    set_global_hub(hub)
    ResourceHub.set_instance(hub)

    llm = hub.llm("gpt-4o")

    messages_list = [
        [{"role": "user", "content": "Say 'hello' in one word"}],
        [{"role": "user", "content": "Say 'goodbye' in one word"}],
        [{"role": "user", "content": "What is 2+2? Answer with just the number."}],
    ]

    # Test concurrent async (fast)
    print("=" * 60)
    print("TEST: Concurrent async execution (asyncio.gather)")
    print("=" * 60)

    start = time.time()
    results = await asyncio.gather(*[
        llm.generate(messages=msgs, temperature=0.0)
        for msgs in messages_list
    ])
    elapsed = time.time() - start

    print(f"Completed in {elapsed:.2f} seconds")
    for i, result in enumerate(results):
        print(f"  [{i}] {result.choices[0].message.content}")

    await llm.close()


if __name__ == "__main__":
    print("Choose test mode:")
    print("1. Test OpenAI Batch API (slow, 50% cheaper)")
    print("2. Test concurrent async (fast)")
    print()

    choice = input("Enter choice (1 or 2): ").strip()

    if choice == "1":
        asyncio.run(test_batch_backend())
    else:
        asyncio.run(test_concurrent_comparison())
