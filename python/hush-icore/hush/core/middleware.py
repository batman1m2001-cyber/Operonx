"""Middleware system for the Hush engine.

Middleware provides hooks into the engine execution lifecycle:
- before_run: modify inputs before execution
- after_run: modify results after execution
- on_error: handle errors during execution
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict

if TYPE_CHECKING:
    from hush.core.ops.graph.graph_op import GraphOp


class Middleware:
    """Base class for engine middleware.

    Subclass and override any of the hooks to add behavior.
    All hooks are async and called in order (before_run) or
    reverse order (after_run, on_error).
    """

    async def before_run(
        self, graph: GraphOp, inputs: Dict[str, Any], context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Called before graph execution. Can modify inputs.

        Args:
            graph: The GraphOp being executed
            inputs: The input dict (modify and return)
            context: Execution context (user_id, session_id, request_id, etc.)

        Returns:
            The (possibly modified) inputs dict.
        """
        return inputs

    async def after_run(
        self,
        graph: GraphOp,
        inputs: Dict[str, Any],
        result: Dict[str, Any],
        context: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Called after graph execution. Can modify result.

        Args:
            graph: The GraphOp that was executed
            inputs: The original inputs
            result: The result dict (modify and return)
            context: Execution context

        Returns:
            The (possibly modified) result dict.
        """
        return result

    async def on_error(
        self, graph: GraphOp, inputs: Dict[str, Any], error: Exception, context: Dict[str, Any]
    ) -> None:
        """Called when graph execution fails.

        Default behavior re-raises the error. Override to add
        logging, alerting, or error transformation.

        Args:
            graph: The GraphOp that failed
            inputs: The original inputs
            error: The exception that occurred
            context: Execution context
        """
        raise error
