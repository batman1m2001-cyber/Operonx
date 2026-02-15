"""Hush - Workflow execution engine.

This module provides the Hush class, an execution engine that runs
GraphOp workflows with state management and observability.

Example:
    ```python
    from hush.core import Hush, GraphOp, START, END, PARENT
    from hush.core.ops import FuncOp

    # Define graph
    with GraphOp(name="my-workflow") as graph:
        node = FuncOp(name="processor", ...)
        START >> node >> END

    # Create engine and run
    engine = Hush(graph)
    result = await engine.run(inputs={"query": "hello"})
    print(result["answer"])  # workflow output
    print(result["$state"])  # access state for debugging/tracing
    ```
"""

import uuid
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from hush.core.loggings import LOGGER
from hush.core.ops.graph.graph_op import GraphOp
from hush.core.states import StateSchema
from hush.core.streams import STREAM_SERVICE

if TYPE_CHECKING:
    from hush.core.tracing import Tracer


class Hush:
    """Workflow execution engine.

    Hush takes a GraphOp and provides execution capabilities:
    - Builds and validates the graph structure
    - Creates state schema for data flow
    - Executes workflows with fresh state per run
    - Integrates with tracers for observability

    Attributes:
        graph: The GraphOp to execute
        name: Workflow name (from graph)
        schema: State schema for the workflow

    Example:
        ```python
        # Define graph
        with GraphOp(name="chatbot") as graph:
            prompt = PromptOp(name="prompt", ...)
            llm = LLMOp(name="llm", ...)
            START >> prompt >> llm >> END

        # Create engine (builds automatically)
        engine = Hush(graph)

        # Run multiple times with fresh state
        result = await engine.run(inputs={"query": "Hello!"})
        print(result["response"])      # workflow output
        print(result["$state"])        # MemoryState for debugging

        # Or use callable syntax
        result = await engine({"query": "Goodbye!"})
        ```
    """

    __slots__ = ["graph", "name", "_schema"]

    def __init__(self, graph: GraphOp):
        """Initialize Hush engine with a GraphOp.

        Args:
            graph: The GraphOp workflow to execute.
                   Must be defined (context manager exited).
        """
        self.graph = graph
        self.name = graph.name

        # Build graph and create schema immediately
        self.graph.build()
        self._schema = StateSchema(self.graph)

        LOGGER.debug("Hush engine initialized for workflow [highlight]%s[/highlight]", self.name)

    @property
    def schema(self) -> StateSchema:
        """Access the workflow state schema."""
        return self._schema

    async def run(
        self,
        inputs: Dict[str, Any],
        *,
        user_id: Optional[str] = None,
        session_id: Optional[str] = None,
        request_id: Optional[str] = None,
        tracers: Optional[List["Tracer"]] = None,
    ) -> Dict[str, Any]:
        """Execute the workflow with given inputs.

        Each call creates a fresh state, so the same engine can be
        used for multiple independent executions.

        Args:
            inputs: Input data for the workflow
            user_id: Optional user identifier (auto-generated if not provided)
            session_id: Optional session identifier (auto-generated if not provided)
            request_id: Optional request identifier (auto-generated if not provided)
            tracers: Optional list of tracers (e.g., [HushEyesTracer()])

        Returns:
            Dictionary containing workflow outputs plus "$state" key
            with the MemoryState for debugging/tracing access.
        """
        # Generate IDs if not provided
        user_id = user_id or str(uuid.uuid4())
        session_id = session_id or str(uuid.uuid4())
        request_id = request_id or str(uuid.uuid4())

        LOGGER.info(
            "[title]\\[%s][/title] Running workflow [highlight]%s[/highlight]",
            request_id,
            self.name,
        )

        # Create fresh state for this run
        state = self._schema.create_state(
            inputs=inputs,
            user_id=user_id,
            session_id=session_id,
            request_id=request_id,
        )

        # Execute the graph
        result = await self.graph.run(state)

        # End stream for this request
        await STREAM_SERVICE.end_request(request_id, session_id=session_id)

        # Collect + flush in background thread (non-blocking)
        if tracers:
            from hush.core.tracing import get_flush_worker

            get_flush_worker().submit(tracers, self.graph, state)

        LOGGER.info(
            "[title]\\[%s][/title] Workflow [highlight]%s[/highlight] completed",
            request_id,
            self.name,
        )

        # Include state in result for debugging/tracing access
        result["$state"] = state

        return result

    async def __call__(self, inputs: Dict[str, Any], **kwargs) -> Dict[str, Any]:
        """Callable syntax for running the workflow.

        Equivalent to calling run() with the same arguments.

        Args:
            inputs: Input data for the workflow
            **kwargs: Additional arguments passed to run()

        Returns:
            Dictionary containing workflow outputs plus "$state" key
        """
        return await self.run(inputs, **kwargs)

    def show(self) -> None:
        """Display workflow structure for debugging."""
        print(f"\n=== Hush Engine: {self.name} ===")
        self.graph.show()
        print()
        self._schema.show()

    def __repr__(self) -> str:
        return f"<Hush engine='{self.name}' ops={len(self.graph._ops)}>"
