"""Tests for RustMemoryState — Rust-backed state management.

Tests that RustMemoryState is a drop-in replacement for Python MemoryState:
- Basic set/get with 3-tuple keys
- Pull ref resolution (1-hop lazy read)
- Push ref resolution (1-hop write)
- Context isolation (iteration contexts)
- Properties (name, metadata, execution_order, tags)
- Collection interface (__contains__, __len__)
"""

import pytest

from hush_runtime import RustMemoryState


# =============================================================================
# Fixtures: build Python StateSchema for testing
# =============================================================================


@pytest.fixture
def simple_schema():
    """Schema with 2 ops, no refs."""
    from hush.core.states.schema import StateSchema

    schema = StateSchema(name="test_workflow")
    schema._register("test_workflow", "input_x", None)
    schema._register("op_a", "x", None)
    schema._register("op_a", "result", None)
    schema._register("op_a", "start_time", None)
    schema._register("op_a", "end_time", None)
    schema._register("op_a", "duration_ms", None)
    schema._register("op_a", "cost_usd", None)
    schema._register("op_a", "error", None)
    schema._build()
    return schema


@pytest.fixture
def schema_with_refs():
    """Schema with pull ref: op_a.x pulls from test_workflow.input_x."""
    from hush.core.states.ref import Ref
    from hush.core.states.schema import StateSchema

    schema = StateSchema(name="test_workflow")
    # test_workflow.input_x is a source
    schema._register("test_workflow", "input_x", None)
    # op_a.x has a pull ref to test_workflow.input_x
    ref = Ref("test_workflow", "input_x")
    schema._register("op_a", "x", ref)
    schema._register("op_a", "result", None)
    schema._register("op_a", "start_time", None)
    schema._register("op_a", "end_time", None)
    schema._register("op_a", "duration_ms", None)
    schema._register("op_a", "cost_usd", None)
    schema._register("op_a", "error", None)
    schema._build()
    return schema


@pytest.fixture
def schema_with_push_ref():
    """Schema with push ref: op_a.result pushes to test_workflow.output."""
    from hush.core.states.ref import Ref
    from hush.core.states.schema import StateSchema

    schema = StateSchema(name="test_workflow")
    schema._register("test_workflow", "input_x", None)
    schema._register("test_workflow", "output", None)
    schema._register("op_a", "x", None)
    schema._register("op_a", "result", None)
    # Push ref: op_a.result → test_workflow.output
    push_ref = Ref("test_workflow", "output", is_output=True)
    schema._register_push_ref("op_a", "result", push_ref)
    schema._register("op_a", "start_time", None)
    schema._register("op_a", "end_time", None)
    schema._register("op_a", "duration_ms", None)
    schema._register("op_a", "cost_usd", None)
    schema._register("op_a", "error", None)
    schema._build()
    return schema


# =============================================================================
# Basic construction and properties
# =============================================================================


class TestConstruction:
    def test_from_schema_basic(self, simple_schema):
        state = RustMemoryState.from_schema(simple_schema)
        assert state.name == "test_workflow"
        assert len(state) > 0

    def test_from_schema_with_inputs(self, simple_schema):
        state = RustMemoryState.from_schema(simple_schema, inputs={"input_x": 42})
        assert state["test_workflow", "input_x", None] == 42

    def test_from_schema_with_ids(self, simple_schema):
        state = RustMemoryState.from_schema(
            simple_schema,
            user_id="user-1",
            session_id="sess-1",
            request_id="req-1",
        )
        assert state.user_id == "user-1"
        assert state.session_id == "sess-1"
        assert state.request_id == "req-1"

    def test_auto_generated_ids(self, simple_schema):
        state = RustMemoryState.from_schema(simple_schema)
        assert len(state.user_id) > 0
        assert len(state.session_id) > 0
        assert len(state.request_id) > 0
        # Should be UUIDs
        assert "-" in state.user_id

    def test_schema_property(self, simple_schema):
        state = RustMemoryState.from_schema(simple_schema)
        assert state.schema is simple_schema


# =============================================================================
# Core get/set
# =============================================================================


class TestGetSet:
    def test_setitem_getitem(self, simple_schema):
        state = RustMemoryState.from_schema(simple_schema)
        state["op_a", "result", None] = 42
        assert state["op_a", "result", None] == 42

    def test_setitem_getitem_with_context(self, simple_schema):
        state = RustMemoryState.from_schema(simple_schema)
        state["op_a", "result", "iter[0]"] = 10
        state["op_a", "result", "iter[1]"] = 20
        assert state["op_a", "result", "iter[0]"] == 10
        assert state["op_a", "result", "iter[1]"] == 20

    def test_getitem_missing_returns_none(self, simple_schema):
        state = RustMemoryState.from_schema(simple_schema)
        assert state["op_a", "result", None] is None

    def test_getitem_unknown_op_returns_none(self, simple_schema):
        state = RustMemoryState.from_schema(simple_schema)
        assert state["nonexistent", "var", None] is None

    def test_setitem_unknown_op_raises(self, simple_schema):
        state = RustMemoryState.from_schema(simple_schema)
        with pytest.raises(KeyError):
            state["nonexistent", "var", None] = 42

    def test_get_method(self, simple_schema):
        state = RustMemoryState.from_schema(simple_schema)
        state["op_a", "result", None] = "hello"
        assert state.get("op_a", "result") == "hello"

    def test_has_method(self, simple_schema):
        state = RustMemoryState.from_schema(simple_schema)
        assert state.has("op_a", "result") is False
        state["op_a", "result", None] = 42
        assert state.has("op_a", "result") is True

    def test_various_types(self, simple_schema):
        """Test storing different Python types."""
        state = RustMemoryState.from_schema(simple_schema)

        # int
        state["op_a", "result", None] = 42
        assert state["op_a", "result", None] == 42

        # string
        state["op_a", "result", None] = "hello"
        assert state["op_a", "result", None] == "hello"

        # list
        state["op_a", "result", None] = [1, 2, 3]
        assert state["op_a", "result", None] == [1, 2, 3]

        # dict
        state["op_a", "result", None] = {"key": "value"}
        assert state["op_a", "result", None] == {"key": "value"}


# =============================================================================
# Pull ref resolution
# =============================================================================


class TestPullRef:
    def test_pull_ref_basic(self, schema_with_refs):
        state = RustMemoryState.from_schema(schema_with_refs, inputs={"input_x": 42})
        # op_a.x has a pull ref to test_workflow.input_x
        # Reading op_a.x should pull from test_workflow.input_x
        assert state["op_a", "x", None] == 42

    def test_pull_ref_caches(self, schema_with_refs):
        state = RustMemoryState.from_schema(schema_with_refs, inputs={"input_x": 42})
        # First read triggers pull
        val1 = state["op_a", "x", None]
        # Second read should be cached
        val2 = state["op_a", "x", None]
        assert val1 == val2 == 42

    def test_pull_ref_with_context(self, schema_with_refs):
        state = RustMemoryState.from_schema(schema_with_refs)
        # Set source with specific context
        state["test_workflow", "input_x", "iter[0]"] = 10
        state["test_workflow", "input_x", "iter[1]"] = 20
        # Pull should use the same context
        assert state["op_a", "x", "iter[0]"] == 10
        assert state["op_a", "x", "iter[1]"] == 20


# =============================================================================
# Push ref resolution
# =============================================================================


class TestPushRef:
    def test_push_ref_basic(self, schema_with_push_ref):
        state = RustMemoryState.from_schema(schema_with_push_ref)
        # Writing to op_a.result should push to test_workflow.output
        state["op_a", "result", None] = 42
        assert state["test_workflow", "output", None] == 42

    def test_push_ref_with_context(self, schema_with_push_ref):
        state = RustMemoryState.from_schema(schema_with_push_ref)
        state["op_a", "result", "ctx1"] = 10
        state["op_a", "result", "ctx2"] = 20
        assert state["test_workflow", "output", "ctx1"] == 10
        assert state["test_workflow", "output", "ctx2"] == 20


# =============================================================================
# Index-based access
# =============================================================================


class TestIndexAccess:
    def test_get_by_index(self, simple_schema):
        state = RustMemoryState.from_schema(simple_schema, inputs={"input_x": 42})
        idx = simple_schema.get_index("test_workflow", "input_x")
        assert state.get_by_index(idx) == 42

    def test_set_by_index(self, simple_schema):
        state = RustMemoryState.from_schema(simple_schema)
        idx = simple_schema.get_index("op_a", "result")
        state.set_by_index(idx, 99)
        assert state["op_a", "result", None] == 99

    def test_index_out_of_range(self, simple_schema):
        state = RustMemoryState.from_schema(simple_schema)
        with pytest.raises(IndexError):
            state.get_by_index(9999)


# =============================================================================
# Execution tracking
# =============================================================================


class TestExecutionTracking:
    def test_record_execution(self, simple_schema):
        state = RustMemoryState.from_schema(simple_schema)
        state.record_execution("op_a", "test_workflow", "main")
        order = state.execution_order
        assert len(order) == 1
        assert order[0]["op"] == "op_a"
        assert order[0]["parent"] == "test_workflow"
        assert order[0]["context_id"] == "main"

    def test_multiple_executions(self, simple_schema):
        state = RustMemoryState.from_schema(simple_schema)
        state.record_execution("op_a")
        state.record_execution("op_b")
        assert len(state.execution_order) == 2


# =============================================================================
# Tags
# =============================================================================


class TestTags:
    def test_add_tag(self, simple_schema):
        state = RustMemoryState.from_schema(simple_schema)
        state.add_tag("error")
        assert "error" in state.tags

    def test_add_tag_dedup(self, simple_schema):
        state = RustMemoryState.from_schema(simple_schema)
        state.add_tag("error")
        state.add_tag("error")
        assert state.tags.count("error") == 1

    def test_add_tags(self, simple_schema):
        state = RustMemoryState.from_schema(simple_schema)
        state.add_tags(["a", "b", "c"])
        assert state.tags == ["a", "b", "c"]


# =============================================================================
# Collection interface
# =============================================================================


class TestCollection:
    def test_contains(self, simple_schema):
        state = RustMemoryState.from_schema(simple_schema)
        assert ("op_a", "result") in state
        assert ("nonexistent", "var") not in state

    def test_len(self, simple_schema):
        state = RustMemoryState.from_schema(simple_schema)
        assert len(state) == len(simple_schema)

    def test_repr(self, simple_schema):
        state = RustMemoryState.from_schema(simple_schema)
        assert "RustMemoryState" in repr(state)
        assert "test_workflow" in repr(state)

    def test_context_manager(self, simple_schema):
        with RustMemoryState.from_schema(simple_schema) as state:
            state["op_a", "result", None] = 42
            assert state["op_a", "result", None] == 42


# =============================================================================
# Metadata
# =============================================================================


class TestMetadata:
    def test_metadata_dict(self, simple_schema):
        state = RustMemoryState.from_schema(
            simple_schema,
            user_id="u1",
            session_id="s1",
            request_id="r1",
        )
        meta = state.metadata
        assert meta["user_id"] == "u1"
        assert meta["session_id"] == "s1"
        assert meta["request_id"] == "r1"
