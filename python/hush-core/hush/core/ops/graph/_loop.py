"""Feedback-loop execution for GraphOp.

Separated from graph_op.py to keep the main class focused on graph lifecycle.
"""

from dataclasses import dataclass, field
from typing import Any

from hush.core.ops.graph.scheduler import run_scheduler


@dataclass
class LoopConfig:
    """Configuration for GraphOp.loop() feedback loops."""

    until: Any  # str expression or callable
    max_iterations: int
    initial_state: dict
    _compiled_until: Any = field(default=None, repr=False)


def evaluate_until(cfg: LoopConfig, outputs: dict) -> bool:
    """Evaluate the loop's until condition against current outputs."""
    if cfg is None or cfg.until is None:
        return False
    if callable(cfg.until):
        return bool(cfg.until(outputs))
    return bool(eval(cfg._compiled_until, {"__builtins__": {}}, outputs))  # noqa: S307


async def run_loop(graph, state, context_id, parent_context, request_id, outputs):
    """Run feedback loop iterations until condition met or max reached.

    Returns:
        Final outputs dict with _loop_metrics added.
    """
    cfg = graph._loop_config
    iteration = 0
    while True:
        if evaluate_until(cfg, outputs):
            outputs["_loop_metrics"] = {
                "total_iterations": iteration + 1,
                "stopped_by_condition": True,
            }
            return outputs
        iteration += 1
        if iteration >= cfg.max_iterations:
            outputs["_loop_metrics"] = {
                "total_iterations": iteration,
                "stopped_by_condition": False,
                "max_iterations_reached": True,
            }
            return outputs
        next_ctx = context_id + (f"loop_{iteration}",)
        for var_name, value in outputs.items():
            state[graph.full_name, var_name, next_ctx] = value
        outputs, _ = await run_scheduler(graph, state, next_ctx, parent_context, request_id)
