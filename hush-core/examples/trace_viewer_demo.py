"""Demo script to generate traces for the Hush Trace Viewer UI.

This script demonstrates:
- Various node types (CodeNode, ForLoopNode, MapNode, WhileLoopNode)
- Static tags (set on tracer initialization)
- Dynamic tags (returned via $tags in node output)
- Nested loops
- Different users and sessions

Generates traces to HUSH_TRACES_DB (default: ~/.hush/traces.db).
"""

import asyncio
from time import sleep

from hush.core import (
    Hush,
    GraphNode,
    CodeNode,
    START, END, PARENT,
)
from hush.core.nodes.iteration.for_loop_node import ForLoopNode
from hush.core.nodes.iteration.map_node import MapNode
from hush.core.nodes.iteration.while_loop_node import WhileLoopNode
from hush.core.nodes.iteration.base import Each
from hush.core.nodes.transform.code_node import code_node
from hush.core.tracers import LocalTracer


# ============================================================================
# Code Nodes with Dynamic Tags
# ============================================================================

@code_node
def preprocess(text: str):
    """Preprocess input text with dynamic tags."""
    sleep(0.01)
    cleaned = text.strip().lower()
    # Add dynamic tag based on text length
    tags = ["preprocessed"]
    if len(cleaned) > 30:
        tags.append("long-text")
    return {"cleaned": cleaned, "$tags": tags}


@code_node
def tokenize(text: str):
    """Split text into tokens."""
    sleep(0.02)
    tokens = text.split()
    count = len(tokens)
    # Dynamic tags based on token count
    tags = ["tokenized"]
    if count > 5:
        tags.append("many-tokens")
    return {"tokens": tokens, "count": count, "$tags": tags}


@code_node
def analyze_token(token: str, multiplier: int):
    """Analyze a single token."""
    sleep(0.005)
    score = len(token) * multiplier
    return {"score": score}


@code_node
def aggregate_scores(scores: list):
    """Aggregate all scores with dynamic tags."""
    sleep(0.01)
    total = sum(scores) if scores else 0
    avg = total / len(scores) if scores else 0
    # Tag based on score
    tags = ["aggregated"]
    if avg > 20:
        tags.append("high-avg-score")
    return {"total": total, "average": avg, "$tags": tags}


@code_node
def classify(score: float):
    """Classify based on score with dynamic category tag."""
    sleep(0.01)
    if score > 50:
        category, confidence = "high", 0.9
    elif score > 25:
        category, confidence = "medium", 0.7
    else:
        category, confidence = "low", 0.5
    # Dynamic tag for category
    return {"category": category, "confidence": confidence, "$tags": [f"category:{category}"]}


@code_node
def format_output(category: str, confidence: float, total: int):
    """Format final output."""
    sleep(0.01)
    return {
        "result": f"{category} (confidence: {confidence:.0%})",
        "details": {"category": category, "confidence": confidence, "total_score": total}
    }


@code_node
def halve_value(value: int):
    """Halve a value (for while loop demo)."""
    sleep(0.005)
    new_val = value // 2
    # Tag when value gets small
    tags = []
    if new_val < 10:
        tags.append("small-value")
    return {"new_value": new_val, "$tags": tags} if tags else {"new_value": new_val}


# ============================================================================
# Build Workflows
# ============================================================================

def build_text_analysis_workflow():
    """Build text analysis workflow with MapNode."""
    with GraphNode(name="text-analysis-pipeline") as graph:
        preprocess_node = preprocess(
            name="preprocess",
            inputs={"text": PARENT["input_text"]},
        )

        tokenize_node = tokenize(
            name="tokenize",
            inputs={"text": preprocess_node["cleaned"]},
        )

        with MapNode(
            name="analyze_tokens",
            inputs={
                "token": Each(tokenize_node["tokens"]),
                "multiplier": PARENT["multiplier"],
            }
        ) as map_node:
            analyze = analyze_token(
                name="analyze",
                inputs={
                    "token": PARENT["token"],
                    "multiplier": PARENT["multiplier"],
                },
                outputs={"*": PARENT},
            )
            START >> analyze >> END

        aggregate_node = aggregate_scores(
            name="aggregate",
            inputs={"scores": map_node["score"]},
        )

        classify_node = classify(
            name="classify",
            inputs={"score": aggregate_node["average"]},
        )

        format_node = format_output(
            name="format",
            inputs={
                "category": classify_node["category"],
                "confidence": classify_node["confidence"],
                "total": aggregate_node["total"],
            },
            outputs={"*": PARENT},
        )

        START >> preprocess_node >> tokenize_node >> map_node >> aggregate_node >> classify_node >> format_node >> END

    return graph


def build_nested_loop_workflow():
    """Build workflow with nested ForLoops."""

    @code_node
    def validate(x: int):
        sleep(0.005)
        return {"validated_x": x, "$tags": ["validated"]}

    @code_node
    def multiply(x: int, y: int):
        sleep(0.005)
        product = x * y
        tags = ["multiplied"]
        if product > 50:
            tags.append("large-product")
        return {"product": product, "$tags": tags}

    @code_node
    def summarize(products: list):
        sleep(0.006)
        total = sum(products) if products else 0
        return {"total": total}

    with GraphNode(name="nested-loop-demo") as graph:
        with ForLoopNode(
            name="outer_loop",
            inputs={"x": Each([2, 3])}
        ) as outer:
            validate_node = validate(
                name="validate",
                inputs={"x": PARENT["x"]},
            )

            with ForLoopNode(
                name="inner_loop",
                inputs={
                    "y": Each([10, 20]),
                    "x": validate_node["validated_x"],
                }
            ) as inner:
                mult_node = multiply(
                    name="multiply",
                    inputs={"x": PARENT["x"], "y": PARENT["y"]},
                    outputs={"*": PARENT},
                )
                START >> mult_node >> END

            summarize_node = summarize(
                name="summarize",
                inputs={"products": inner["product"]},
                outputs={"*": PARENT},
            )

            START >> validate_node >> inner >> summarize_node >> END

        outer["total"] >> PARENT["results"]
        START >> outer >> END

    return graph


def build_while_loop_workflow():
    """Build workflow with WhileLoopNode."""
    with GraphNode(name="while-loop-demo") as graph:
        with WhileLoopNode(
            name="halve_loop",
            inputs={"value": PARENT["start_value"]},
            stop_condition="value < 5",
            max_iterations=10,
        ) as while_loop:
            halve_node = halve_value(
                name="halve",
                inputs={"value": PARENT["value"]},
            )
            halve_node["new_value"] >> PARENT["value"]
            START >> halve_node >> END

        while_loop["value"] >> PARENT["final_value"]
        START >> while_loop >> END

    return graph


# ============================================================================
# Run Demo
# ============================================================================

async def run_demo():
    """Run demo workflows with different tag configurations."""

    print("Hush Trace Viewer Demo")
    print("=" * 60)
    print("Generating traces with static and dynamic tags...")
    print()

    # Build workflows
    text_workflow = build_text_analysis_workflow()
    nested_workflow = build_nested_loop_workflow()
    while_workflow = build_while_loop_workflow()

    # Create engines
    text_engine = Hush(text_workflow)
    nested_engine = Hush(nested_workflow)
    while_engine = Hush(while_workflow)

    # ========================================================================
    # Case 1: Text analysis with static tags (production environment)
    # ========================================================================
    print("1. Text analysis - production environment (static tags)")
    tracer_prod = LocalTracer(name="prod-tracer", tags=["production", "ml-team"])

    result = await text_engine.run(
        inputs={"input_text": "Machine learning is transforming industries", "multiplier": 3},
        request_id="text-prod-001",
        user_id="user-alice",
        session_id="session-prod-1",
        tracer=tracer_prod,
    )
    print(f"   Result: {result.get('result')}")

    # ========================================================================
    # Case 2: Text analysis with different static tags (staging)
    # ========================================================================
    print("2. Text analysis - staging environment (different static tags)")
    tracer_staging = LocalTracer(name="staging-tracer", tags=["staging", "qa-team"])

    result = await text_engine.run(
        inputs={"input_text": "Hello world test", "multiplier": 2},
        request_id="text-staging-001",
        user_id="user-bob",
        session_id="session-staging-1",
        tracer=tracer_staging,
    )
    print(f"   Result: {result.get('result')}")

    # ========================================================================
    # Case 3: Text analysis - long text triggers dynamic tag
    # ========================================================================
    print("3. Text analysis - long text (triggers 'long-text' dynamic tag)")
    tracer_dev = LocalTracer(name="dev-tracer", tags=["development"])

    result = await text_engine.run(
        inputs={
            "input_text": "The quick brown fox jumps over the lazy dog and runs away",
            "multiplier": 4
        },
        request_id="text-dev-001",
        user_id="user-charlie",
        session_id="session-dev-1",
        tracer=tracer_dev,
    )
    print(f"   Result: {result.get('result')}")

    # ========================================================================
    # Case 4: Nested loops with static tags
    # ========================================================================
    print("4. Nested loop workflow (static tags: 'experiment', 'batch-job')")
    tracer_experiment = LocalTracer(name="experiment-tracer", tags=["experiment", "batch-job"])

    result = await nested_engine.run(
        inputs={},
        request_id="nested-001",
        user_id="user-alice",
        session_id="session-experiment-1",
        tracer=tracer_experiment,
    )
    print(f"   Results: {result.get('results')}")

    # ========================================================================
    # Case 5: While loop - high start value (many iterations)
    # ========================================================================
    print("5. While loop - high start value (triggers 'small-value' tag near end)")
    tracer_loop = LocalTracer(name="loop-tracer", tags=["iteration-test"])

    result = await while_engine.run(
        inputs={"start_value": 100},
        request_id="while-high-001",
        user_id="user-bob",
        session_id="session-loop-1",
        tracer=tracer_loop,
    )
    print(f"   100 -> {result.get('final_value')}")

    # ========================================================================
    # Case 6: While loop - low start value (few iterations)
    # ========================================================================
    print("6. While loop - low start value (fewer iterations)")

    result = await while_engine.run(
        inputs={"start_value": 20},
        request_id="while-low-001",
        user_id="user-charlie",
        session_id="session-loop-2",
        tracer=tracer_loop,
    )
    print(f"   20 -> {result.get('final_value')}")

    # ========================================================================
    # Case 7: Mixed tags - static + dynamic combination
    # ========================================================================
    print("7. Text analysis - mixed static/dynamic tags with high score")
    tracer_mixed = LocalTracer(name="mixed-tracer", tags=["monitoring", "alerts-enabled"])

    result = await text_engine.run(
        inputs={
            "input_text": "Artificial intelligence deep learning neural networks powerful",
            "multiplier": 5
        },
        request_id="text-mixed-001",
        user_id="user-alice",
        session_id="session-mixed-1",
        tracer=tracer_mixed,
    )
    print(f"   Result: {result.get('result')}")

    # ========================================================================
    # Case 8: Same workflow, different user
    # ========================================================================
    print("8. Same workflow, different user (user tracking)")

    result = await text_engine.run(
        inputs={"input_text": "Cloud computing scalability", "multiplier": 2},
        request_id="text-mixed-002",
        user_id="user-david",
        session_id="session-mixed-2",
        tracer=tracer_mixed,
    )
    print(f"   Result: {result.get('result')}")

    # ========================================================================
    # Finish
    # ========================================================================
    print()
    print("=" * 60)
    print("Generated 8 diverse traces!")
    print("Waiting for background process to flush...")

    sleep(3)  # Give time for background to write all traces

    print()
    from hush.core.background import DEFAULT_DB_PATH
    print(f"Traces saved to {DEFAULT_DB_PATH}")


def main():
    """Main entry point - run demo to generate traces."""
    import os
    from pathlib import Path

    # Remove old traces database for a clean demo
    from hush.core.background import DEFAULT_DB_PATH
    db_path = Path(os.environ.get("HUSH_TRACES_DB", DEFAULT_DB_PATH))
    if db_path.exists():
        db_path.unlink()
        print(f"Removed old database: {db_path}")
        print()

    # Run the demo to generate traces
    asyncio.run(run_demo())

    print()
    print("To view traces, use VS Code command: 'Hush: Open Traces'")


if __name__ == "__main__":
    main()
