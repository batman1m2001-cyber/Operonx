"""Tests for MemoryState - workflow state with Cell-based storage."""

import pytest
from hush.core.states.schema import StateSchema
from hush.core.states.state import MemoryState
from hush.core.nodes.graph.graph_node import GraphNode, START, END, PARENT
from hush.core.nodes.transform.code_node import CodeNode


# ============================================================
# Test 1: Simple Linear Graph Value Flow
# ============================================================

class TestSimpleLinearGraphValueFlow:
    """Test value injection and ref following in linear graph."""

    def test_input_set(self):
        """Test that input values are set correctly."""
        with GraphNode(name="linear_graph") as graph:
            node_a = CodeNode(
                name="node_a",
                code_fn=lambda x: {"result": x + 10},
                inputs={"x": PARENT["x"]}
            )
            START >> node_a >> END

        graph.build()
        schema = StateSchema(graph)
        state = MemoryState(schema, inputs={"x": 5})

        assert state["linear_graph", "x", None] == 5

    def test_ref_resolution(self):
        """Test that refs are resolved correctly."""
        with GraphNode(name="linear_graph") as graph:
            node_a = CodeNode(
                name="node_a",
                code_fn=lambda x: {"result": x + 10},
                inputs={"x": PARENT["x"]}
            )
            START >> node_a >> END

        graph.build()
        schema = StateSchema(graph)
        state = MemoryState(schema, inputs={"x": 5})

        # node_a.x should resolve to linear_graph.x
        x_val = state["linear_graph.node_a", "x", None]
        assert x_val == 5

    def test_value_flow_through_nodes(self):
        """Test value flow through multiple nodes."""
        with GraphNode(name="linear_graph") as graph:
            node_a = CodeNode(
                name="node_a",
                code_fn=lambda x: {"result": x + 10},
                inputs={"x": PARENT["x"]}
            )
            node_b = CodeNode(
                name="node_b",
                code_fn=lambda x: {"result": x * 2},
                inputs={"x": node_a["result"]}
            )
            START >> node_a >> node_b >> END

        graph.build()
        schema = StateSchema(graph)
        state = MemoryState(schema, inputs={"x": 5})

        # Simulate node_a execution
        x_val = state["linear_graph.node_a", "x", None]
        state["linear_graph.node_a", "result", None] = x_val + 10  # 15

        # Simulate node_b reading from node_a
        x_val = state["linear_graph.node_b", "x", None]
        assert x_val == 15


# ============================================================
# Test 2: Ref with Operations
# ============================================================

class TestRefWithOperations:
    """Test ref with operations applied during resolution."""

    def test_getitem_applied(self):
        """Test getitem operations are applied."""
        with GraphNode(name="ref_ops_graph") as graph:
            node_a = CodeNode(
                name="data_source",
                code_fn=lambda: {"data": {"items": [1, 2, 3], "name": "test"}},
                inputs={}
            )
            node_b = CodeNode(
                name="extract_items",
                code_fn=lambda items: {"count": len(items)},
                inputs={"items": node_a["data"]["items"]}
            )
            START >> node_a >> [node_b] >> END

        graph.build()
        schema = StateSchema(graph)
        state = MemoryState(schema)

        # Inject data_source output
        state["ref_ops_graph.data_source", "data", None] = {"items": [10, 20, 30], "name": "hello"}

        # Read with ops applied
        items = state["ref_ops_graph.extract_items", "items", None]
        assert items == [10, 20, 30]

    def test_method_call_applied(self):
        """Test method call operations are applied."""
        with GraphNode(name="ref_ops_graph") as graph:
            node_a = CodeNode(
                name="data_source",
                code_fn=lambda: {"data": {"name": "test"}},
                inputs={}
            )
            node_b = CodeNode(
                name="transform_name",
                code_fn=lambda name: {"upper_name": name},
                inputs={"name": node_a["data"]["name"].upper()}
            )
            START >> node_a >> [node_b] >> END

        graph.build()
        schema = StateSchema(graph)
        state = MemoryState(schema)

        # Inject data
        state["ref_ops_graph.data_source", "data", None] = {"items": [10, 20, 30], "name": "hello"}

        # Read with method call applied
        name = state["ref_ops_graph.transform_name", "name", None]
        assert name == "HELLO"


# ============================================================
# Test 3: Ref with Apply
# ============================================================

class TestRefWithApply:
    """Test ref with apply() function during resolution."""

    def test_apply_functions(self):
        """Test apply(len), apply(sorted), apply(sum)."""
        with GraphNode(name="ref_apply_graph") as graph:
            node_a = CodeNode(
                name="list_source",
                code_fn=lambda: {"numbers": [5, 2, 8, 1, 9, 3]},
                inputs={}
            )
            node_b = CodeNode(
                name="get_length",
                code_fn=lambda length: {"length": length},
                inputs={"length": node_a["numbers"].apply(len)}
            )
            node_c = CodeNode(
                name="sort_numbers",
                code_fn=lambda sorted_nums: {"sorted": sorted_nums},
                inputs={"sorted_nums": node_a["numbers"].apply(sorted)}
            )
            node_d = CodeNode(
                name="sum_numbers",
                code_fn=lambda total: {"total": total},
                inputs={"total": node_a["numbers"].apply(sum)}
            )
            START >> node_a >> [node_b, node_c, node_d] >> END

        graph.build()
        schema = StateSchema(graph)
        state = MemoryState(schema)

        # Inject list_source output
        state["ref_apply_graph.list_source", "numbers", None] = [5, 2, 8, 1, 9, 3]

        # Read with apply() fns
        assert state["ref_apply_graph.get_length", "length", None] == 6
        assert state["ref_apply_graph.sort_numbers", "sorted_nums", None] == [1, 2, 3, 5, 8, 9]
        assert state["ref_apply_graph.sum_numbers", "total", None] == 28


# ============================================================
# Test 4: Multiple Contexts (Loop Simulation)
# ============================================================

class TestMultipleContexts:
    """Test state with multiple contexts (loop iterations)."""

    def test_different_context_values(self):
        """Test that different contexts have independent values."""
        with GraphNode(name="loop_graph") as graph:
            node_a = CodeNode(
                name="accumulator",
                code_fn=lambda x: {"result": x},
                inputs={"x": PARENT["x"]}
            )
            START >> node_a >> END

        graph.build()
        schema = StateSchema(graph)
        state = MemoryState(schema, inputs={"x": 0})

        # Simulate loop iterations
        state["loop_graph", "x", "iter_0"] = 10
        state["loop_graph", "x", "iter_1"] = 20
        state["loop_graph", "x", "iter_2"] = 30

        assert state["loop_graph", "x", "iter_0"] == 10
        assert state["loop_graph", "x", "iter_1"] == 20
        assert state["loop_graph", "x", "iter_2"] == 30

    def test_refs_work_per_context(self):
        """Test that refs work correctly per context."""
        with GraphNode(name="loop_graph") as graph:
            node_a = CodeNode(
                name="accumulator",
                code_fn=lambda x: {"result": x},
                inputs={"x": PARENT["x"]}
            )
            START >> node_a >> END

        graph.build()
        schema = StateSchema(graph)
        state = MemoryState(schema, inputs={"x": 0})

        # Set different contexts
        state["loop_graph", "x", "iter_0"] = 10
        state["loop_graph", "x", "iter_1"] = 20

        # Refs should resolve per context
        assert state["loop_graph.accumulator", "x", "iter_0"] == 10
        assert state["loop_graph.accumulator", "x", "iter_1"] == 20


# ============================================================
# Test 5: Nested Graph Value Flow
# ============================================================

class TestNestedGraphValueFlow:
    """Test value flow in nested graph."""

    def test_nested_ref_resolution(self):
        """Test refs resolve through nested graphs."""
        with GraphNode(name="outer") as outer:
            with GraphNode(
                name="inner",
                inputs={"x": PARENT["x"]},
                outputs={"result": PARENT["inner_result"]}
            ) as inner:
                double = CodeNode(
                    name="double",
                    code_fn=lambda x: {"result": x * 2},
                    inputs={"x": PARENT["x"]},
                    outputs={"result": PARENT["result"]}
                )
                START >> double >> END

            START >> inner >> END

        outer.build()
        schema = StateSchema(outer)
        state = MemoryState(schema, inputs={"x": 7})

        # Verify ref chain works
        assert state["outer", "x", None] == 7
        assert state["outer.inner", "x", None] == 7


# ============================================================
# Test 6: Index-based Access
# ============================================================

class TestIndexBasedAccess:
    """Test direct index-based access."""

    def test_set_and_get_by_index(self):
        """Test set_by_index and get_by_index."""
        schema = StateSchema(name="index_test")
        schema.set("node1", "x", 100)
        schema.set("node1", "y", 200)
        state = MemoryState(schema)

        idx_x = schema.get_index("node1", "x")
        idx_y = schema.get_index("node1", "y")

        state.set_by_index(idx_x, 42)
        state.set_by_index(idx_y, 84)

        assert state.get_by_index(idx_x) == 42
        assert state.get_by_index(idx_y) == 84


# ============================================================
# Test 7: Execution Recording
# ============================================================

class TestExecutionRecording:
    """Test execution order recording."""

    def test_record_execution(self):
        """Test that execution is recorded correctly."""
        schema = StateSchema(name="test")
        schema.set("node", "x", 0)
        state = MemoryState(schema)

        state.record_execution("node1", None, None)
        state.record_execution("node2", "node1", "ctx1")

        assert len(state.execution_order) == 2
        assert state.execution_order[0]["node"] == "node1"
        assert state.execution_order[1]["parent"] == "node1"


# ============================================================
# Test 8: Properties
# ============================================================

class TestProperties:
    """Test state properties."""

    def test_metadata(self):
        """Test metadata property."""
        schema = StateSchema(name="test")
        state = MemoryState(schema, user_id="user1", session_id="sess1", request_id="req1")

        assert state.user_id == "user1"
        assert state.session_id == "sess1"
        assert state.request_id == "req1"

        metadata = state.metadata
        assert metadata["user_id"] == "user1"
        assert metadata["session_id"] == "sess1"
        assert metadata["request_id"] == "req1"

    def test_auto_generated_ids(self):
        """Test that IDs are auto-generated if not provided."""
        schema = StateSchema(name="test")
        state = MemoryState(schema)

        assert state.user_id is not None
        assert state.session_id is not None
        assert state.request_id is not None


# ============================================================
# Test 9: Collection Interface
# ============================================================

class TestCollectionInterface:
    """Test state collection interface."""

    def test_contains(self):
        """Test __contains__ method."""
        with GraphNode(name="test_graph") as graph:
            node = CodeNode(
                name="node",
                code_fn=lambda x: {"y": x},
                inputs={"x": PARENT["x"]}
            )
            START >> node >> END

        graph.build()
        schema = StateSchema(graph)
        state = MemoryState(schema)

        assert ("test_graph", "x") in state
        assert ("nonexistent", "var") not in state

    def test_len(self):
        """Test __len__ method."""
        schema = StateSchema(name="test")
        schema.set("node", "x", 0)
        schema.set("node", "y", 0)
        state = MemoryState(schema)

        assert len(state) == 2

    def test_iter(self):
        """Test __iter__ method."""
        schema = StateSchema(name="test")
        schema.set("node", "x", 0)
        schema.set("node", "y", 0)
        state = MemoryState(schema)

        keys = list(state)
        assert len(keys) == 2


# ============================================================
# Test 10: Context Manager
# ============================================================

class TestContextManager:
    """Test state context manager."""

    def test_with_statement(self):
        """Test using state with 'with' statement."""
        schema = StateSchema(name="test")
        schema.set("node", "x", 0)

        with MemoryState(schema) as state:
            state["node", "x", None] = 42
            assert state["node", "x", None] == 42


# ============================================================
# Test 11: Hash and Equality
# ============================================================

class TestHashAndEquality:
    """Test state hash and equality."""

    def test_hash_based_on_request_id(self):
        """Test that hash is based on request_id."""
        schema = StateSchema(name="test")
        state1 = MemoryState(schema, request_id="same_id")
        state2 = MemoryState(schema, request_id="same_id")

        assert hash(state1) == hash(state2)

    def test_equality_based_on_request_id(self):
        """Test that equality is based on request_id."""
        schema = StateSchema(name="test")
        state1 = MemoryState(schema, request_id="same_id")
        state2 = MemoryState(schema, request_id="same_id")
        state3 = MemoryState(schema, request_id="diff_id")

        assert state1 == state2
        assert state1 != state3
