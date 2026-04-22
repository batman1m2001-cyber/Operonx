"""Tests for the Operon workflow engine."""

import pytest

from operon.core import END, PARENT, START, FuncOp, GraphOp, Operon


class TestOperonBasic:
    """Basic Operon engine tests."""

    def test_hush_creation(self):
        """Test Operon can be created with a GraphOp."""
        with GraphOp(name="test-workflow") as graph:
            node = FuncOp(name="dummy", code_fn=lambda: {"x": 1})
            START >> node >> END

        engine = Operon(graph)
        assert engine.name == "test-workflow"
        assert engine.schema is not None

    def test_hush_repr(self):
        """Test Operon string representation."""
        with GraphOp(name="test") as graph:
            node = FuncOp(name="dummy", code_fn=lambda: {"x": 1})
            START >> node >> END

        engine = Operon(graph)
        assert "test" in repr(engine)
        assert "engine" in repr(engine)


class TestOperonSchema:
    """Test Operon schema creation."""

    def test_schema_created_on_init(self):
        """Test schema is created during Operon initialization."""
        with GraphOp(name="test") as graph:
            node = FuncOp(name="node", code_fn=lambda: {"out": 1})
            START >> node >> END

        engine = Operon(graph)

        assert engine.schema is not None
        assert engine.schema.name == "test"


class TestOperonRun:
    """Test Operon workflow execution."""

    @pytest.mark.asyncio
    async def test_run_simple_workflow(self):
        """Test running a simple workflow that returns constant."""
        with GraphOp(name="test") as graph:
            node = FuncOp(
                name="constant", code_fn=lambda: {"result": 42}, outputs={"result": PARENT}
            )
            START >> node >> END

        engine = Operon(graph)
        result = await engine.run(inputs={})

        assert result["result"] == 42
        assert "$state" in result

    @pytest.mark.asyncio
    async def test_run_generates_ids(self):
        """Test run generates IDs if not provided."""
        with GraphOp(name="test") as graph:
            passthrough = FuncOp(name="passthrough", code_fn=lambda: {})
            START >> passthrough >> END

        engine = Operon(graph)

        # Should not raise
        result = await engine.run(inputs={})
        assert "$state" in result

    @pytest.mark.asyncio
    async def test_run_with_custom_ids(self):
        """Test run with custom IDs."""
        with GraphOp(name="test") as graph:
            passthrough = FuncOp(name="passthrough", code_fn=lambda: {})
            START >> passthrough >> END

        engine = Operon(graph)

        result = await engine.run(
            inputs={}, user_id="user-123", session_id="session-456", request_id="request-789"
        )

        state = result["$state"]
        assert state.user_id == "user-123"
        assert state.session_id == "session-456"
        assert state.request_id == "request-789"

    @pytest.mark.asyncio
    async def test_run_multi_node_pipeline(self):
        """Test running a multi-node pipeline with constant outputs."""
        with GraphOp(name="pipeline") as graph:
            step1 = FuncOp(name="step1", code_fn=lambda: {"value": 10})
            step2 = FuncOp(name="step2", code_fn=lambda: {"final": 20}, outputs={"final": PARENT})
            START >> step1 >> step2 >> END

        engine = Operon(graph)
        result = await engine.run(inputs={})

        assert result["final"] == 20

    @pytest.mark.asyncio
    async def test_run_with_inputs(self):
        """Test running workflow with input data."""
        with GraphOp(name="with-inputs") as graph:
            node = FuncOp(
                name="doubler",
                code_fn=lambda x: {"result": x * 2},
                inputs={"x": PARENT["x"]},
                outputs={"*": PARENT},
            )
            START >> node >> END

        engine = Operon(graph)
        result = await engine.run(inputs={"x": 21})

        assert result["result"] == 42

    @pytest.mark.asyncio
    async def test_callable_syntax(self):
        """Test engine(inputs) callable syntax."""
        with GraphOp(name="callable") as graph:
            node = FuncOp(
                name="adder",
                code_fn=lambda a, b: {"sum": a + b},
                inputs={"a": PARENT["a"], "b": PARENT["b"]},
                outputs={"*": PARENT},
            )
            START >> node >> END

        engine = Operon(graph)
        result = await engine({"a": 10, "b": 5})

        assert result["sum"] == 15


class TestOperonWithTracer:
    """Test Operon with tracer integration."""

    @pytest.mark.asyncio
    async def test_run_with_none_tracer(self):
        """Test run with tracer=None works."""
        with GraphOp(name="test") as graph:
            node = FuncOp(name="node", code_fn=lambda: {"ok": True})
            START >> node >> END

        engine = Operon(graph)
        result = await engine.run(inputs={})

        assert "$state" in result

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_run_with_langfuse_tracer(self):
        """Test running workflow with Langfuse tracer.

        This test pushes traces to Langfuse cloud.
        Run with: pytest -m integration
        """
        import time

        try:
            from operon.telemetry import LangfuseTracer
        except ImportError:
            pytest.skip("operon-telemetry not installed")

        tracer = LangfuseTracer(resource="langfuse:default")

        # Create a multi-step workflow
        with GraphOp(name="operon-integration-test") as graph:
            step1 = FuncOp(
                name="process_input",
                code_fn=lambda: {"processed": "Hello from Operon!"},
                outputs={"processed": PARENT},
            )
            step2 = FuncOp(
                name="transform",
                code_fn=lambda: {"transformed": "Data transformed"},
            )
            step3 = FuncOp(
                name="finalize",
                code_fn=lambda: {"result": "Workflow complete!"},
                outputs={"result": PARENT},
            )
            START >> step1 >> step2 >> step3 >> END

        engine = Operon(graph)

        result = await engine.run(
            inputs={}, tracer=tracer, user_id="test-user", session_id="test-session"
        )

        # Wait for background flush to complete
        time.sleep(2)

        assert result["processed"] == "Hello from Operon!"
        assert result["result"] == "Workflow complete!"
        print("\nTrace pushed to Langfuse: https://cloud.langfuse.com")


class TestOperonShow:
    """Test Operon show/debug methods."""

    def test_show(self, capsys):
        """Test show displays workflow structure."""
        with GraphOp(name="test") as graph:
            node = FuncOp(name="node", code_fn=lambda: {})
            START >> node >> END

        engine = Operon(graph)
        engine.show()

        captured = capsys.readouterr()
        assert "Operon Engine: test" in captured.out


class TestOperonStateAccess:
    """Test accessing state via $state key."""

    @pytest.mark.asyncio
    async def test_state_iter_executed(self):
        """Test iter_executed derives execution from start_time cells."""
        with GraphOp(name="test") as graph:
            a = FuncOp(name="step_a", code_fn=lambda: {"a": 1})
            b = FuncOp(name="step_b", code_fn=lambda: {"b": 2})
            START >> a >> b >> END

        engine = Operon(graph)
        result = await engine.run(inputs={})

        state = result["$state"]

        # Each op that ran should have start_time in state
        executed_a = list(state.iter_executed("test.step_a"))
        executed_b = list(state.iter_executed("test.step_b"))
        assert len(executed_a) >= 1
        assert len(executed_b) >= 1

    @pytest.mark.asyncio
    async def test_state_metadata_contains_ids(self):
        """Test state metadata contains user/session/request IDs."""
        with GraphOp(name="test") as graph:
            node = FuncOp(name="node", code_fn=lambda: {})
            START >> node >> END

        engine = Operon(graph)
        result = await engine.run(inputs={}, user_id="uid", session_id="sid", request_id="rid")

        state = result["$state"]
        assert state.user_id == "uid"
        assert state.session_id == "sid"
        assert state.request_id == "rid"


class TestOperonMultipleRuns:
    """Test running the same engine multiple times."""

    @pytest.mark.asyncio
    async def test_multiple_runs_independent(self):
        """Test each run creates fresh state."""
        with GraphOp(name="counter") as graph:
            node = FuncOp(
                name="echo",
                code_fn=lambda n: {"value": n},
                inputs={"n": PARENT["n"]},
                outputs={"*": PARENT},
            )
            START >> node >> END

        engine = Operon(graph)

        result1 = await engine.run(inputs={"n": 1})
        result2 = await engine.run(inputs={"n": 2})
        result3 = await engine.run(inputs={"n": 3})

        assert result1["value"] == 1
        assert result2["value"] == 2
        assert result3["value"] == 3

        # Each run should have different request IDs
        assert result1["$state"].request_id != result2["$state"].request_id
        assert result2["$state"].request_id != result3["$state"].request_id
