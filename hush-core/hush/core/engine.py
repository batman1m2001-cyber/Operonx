"""Hush - Workflow execution engine.

This module provides the Hush class, an execution engine that runs
GraphNode workflows with state management and observability.

Example:
    ```python
    from hush.core import Hush, GraphNode, START, END, PARENT
    from hush.core.nodes import CodeNode

    # Define graph
    with GraphNode(name="my-workflow") as graph:
        node = CodeNode(name="processor", ...)
        START >> node >> END

    # Create engine and run
    engine = Hush(graph)
    result = await engine.run(inputs={"query": "hello"})
    print(result["answer"])  # workflow output
    print(result["$state"])  # access state for debugging/tracing
    ```
"""

from typing import TYPE_CHECKING, Any, Dict, Optional
import uuid

from hush.core.nodes.graph.graph_node import GraphNode
from hush.core.states import StateSchema
from hush.core.streams import STREAM_SERVICE
from hush.core.loggings import LOGGER
from hush.core.background import get_background

if TYPE_CHECKING:
    from hush.core.tracers import BaseTracer, TraceStore


class Hush:
    """Workflow execution engine.

    Hush takes a GraphNode and provides execution capabilities:
    - Builds and validates the graph structure
    - Creates state schema for data flow
    - Executes workflows with fresh state per run
    - Integrates with tracers for observability

    Attributes:
        graph: The GraphNode to execute
        name: Workflow name (from graph)
        schema: State schema for the workflow

    Example:
        ```python
        # Define graph
        with GraphNode(name="chatbot") as graph:
            prompt = PromptNode(name="prompt", ...)
            llm = LLMNode(name="llm", ...)
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

    def __init__(self, graph: GraphNode):
        """Initialize Hush engine with a GraphNode.

        Args:
            graph: The GraphNode workflow to execute.
                   Must be defined (context manager exited).
        """
        self.graph = graph
        self.name = graph.name

        # Build graph and create schema immediately
        self.graph.build()
        self._schema = StateSchema(self.graph)

        # Eagerly start background process to avoid spawn cost during first node execution
        get_background()._ensure_started()

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
        tracer: Optional["BaseTracer"] = None,
    ) -> Dict[str, Any]:
        """Execute the workflow with given inputs.

        Each call creates a fresh state, so the same engine can be
        used for multiple independent executions.

        Args:
            inputs: Input data for the workflow
            user_id: Optional user identifier (auto-generated if not provided)
            session_id: Optional session identifier (auto-generated if not provided)
            request_id: Optional request identifier (auto-generated if not provided)
            tracer: Optional tracer for observability (e.g., LangfuseTracer)

        Returns:
            Dictionary containing workflow outputs plus "$state" key
            with the MemoryState for debugging/tracing access.
        """
        # Generate IDs if not provided
        user_id = user_id or str(uuid.uuid4())
        session_id = session_id or str(uuid.uuid4())
        request_id = request_id or str(uuid.uuid4())

        LOGGER.info("[title]\\[%s][/title] Running workflow [highlight]%s[/highlight]", request_id, self.name)

        # Create trace store for incremental writes if tracer is provided
        trace_store: Optional["TraceStore"] = None
        if tracer is not None:
            from hush.core.tracers import get_store
            trace_store = get_store()

        # Create fresh state for this run
        state = self._schema.create_state(
            inputs=inputs,
            user_id=user_id,
            session_id=session_id,
            request_id=request_id,
            trace_store=trace_store,
        )

        # Execute the graph
        result = await self.graph.run(state)

        # End stream for this request
        await STREAM_SERVICE.end_request(request_id, session_id=session_id)

        # Fire-and-forget flush to tracer in separate process (non-blocking)
        if tracer is not None:
            tracer.flush_in_background(self.name, state)

        LOGGER.info("[title]\\[%s][/title] Workflow [highlight]%s[/highlight] completed", request_id, self.name)

        # Include state in result for debugging/tracing access
        result["$state"] = state

        return result

    async def __call__(
        self,
        inputs: Dict[str, Any],
        **kwargs
    ) -> Dict[str, Any]:
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
        return f"<Hush engine='{self.name}' nodes={len(self.graph._nodes)}>"
