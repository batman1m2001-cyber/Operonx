"""
Basic example of using Langfuse tracer with ResourceHub.

This example demonstrates:
1. Auto-registration of TracerPlugin
2. Getting tracer from global ResourceHub (loaded from resources.yaml)
3. Adding traces with parent-child relationships
4. Flushing traces to Langfuse
"""

import asyncio

from hush.core.registry import get_hub
from hush.telemetry.models import MessageTraceInfo

# IMPORTANT: Import hush.telemetry FIRST to register TracerPlugin
# before get_hub() creates the global hub
import hush.telemetry  # noqa: F401


async def main():
    # Get the global ResourceHub (loaded from resources.yaml in project root)
    # TracerPlugin is auto-registered when hush.telemetry is imported
    hub = get_hub()

    # Get tracer from ResourceHub using the key from resources.yaml
    # The config at "langfuse:default" will be automatically loaded
    tracer = hub.get("langfuse:default")

    print(f"Created tracer: {tracer}")
    print(f"Tracer type: {type(tracer).__name__}")

    # Create a workflow trace
    request_id = "req_12345"
    workflow_id = "workflow_001"

    # Add workflow span
    tracer.add_span(
        request_id=request_id,
        item_id=workflow_id,
        name="customer_support_workflow",
        metadata={"user_id": "user_789", "session_id": "sess_456"},
    )

    # Add a generation (LLM call) as child of workflow
    generation_id = "gen_001"
    trace_info = MessageTraceInfo(
        conversation_model="gpt-4",
        input=[{"role": "user", "content": "What is the status of my order?"}],
        output={"role": "assistant", "content": "Let me check your order status..."},
        metadata={
            "temperature": 0.7,
            "tokens": {"prompt": 15, "completion": 25, "total": 40},
        },
    )

    tracer.add_generation(
        request_id=request_id,
        item_id=generation_id,
        parent_id=workflow_id,
        name="order_status_query",
        trace_info=trace_info,
    )

    # Add another generation as sibling
    generation_id_2 = "gen_002"
    trace_info_2 = MessageTraceInfo(
        conversation_model="gpt-4",
        input=[{"role": "user", "content": "What is the status of my order status?"}],
        output={"role": "assistant", "content": "Your order has been shipped!"},
        metadata={
            "temperature": 0.7,
            "tokens": {"prompt": 12, "completion": 18, "total": 30},
        },
    )

    tracer.add_generation(
        request_id=request_id,
        item_id=generation_id_2,
        parent_id=workflow_id,
        name="order_status_response",
        trace_info=trace_info_2,
    )

    # Add an event (e.g., database lookup)
    event_id = "evt_001"
    tracer.add_event(
        request_id=request_id,
        item_id=event_id,
        parent_id=generation_id,
        name="database_lookup",
        metadata={"query": "SELECT * FROM orders WHERE user_id=789", "duration_ms": 45},
    )

    print(f"\nAdded traces for request: {request_id}")
    print(f"  - Workflow: {workflow_id}")
    print(f"  - Generation 1: {generation_id}")
    print(f"  - Generation 2: {generation_id_2}")
    print(f"  - Event: {event_id}")

    # Flush traces to Langfuse
    print("\nFlushing traces to Langfuse...")
    success = await tracer.flush(request_id)

    if success:
        print("✓ Successfully flushed traces to Langfuse")
    else:
        print("✗ Failed to flush traces")

    # Flush all remaining traces
    print("\nFlushing all remaining traces...")
    await tracer.flush_all()
    print("✓ All traces flushed")


if __name__ == "__main__":
    # Make sure resources.yaml exists in project root with langfuse:default config
    # Or set HUSH_CONFIG environment variable to point to your config file
    asyncio.run(main())
