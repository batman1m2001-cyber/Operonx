"""Tests for StateSchema - workflow state structure definition."""

import pytest
from hush.core.states.schema import StateSchema
from hush.core.states.ref import Ref
from hush.core.nodes.graph.graph_node import GraphNode, START, END, PARENT
from hush.core.nodes.transform.code_node import CodeNode


# ============================================================
# Test 1: Simple Linear Graph
# ============================================================

class TestSimpleLinearGraph:
    """Test schema creation from linear graph."""

    def test_schema_name(self):
        """Test that schema name matches graph name."""
        with GraphNode(name="linear_graph") as graph:
            node_a = CodeNode(
                name="node_a",
                code_fn=lambda x: {"result": x + 10},
                inputs={"x": PARENT["x"]}
            )
            START >> node_a >> END

        graph.build()
        schema = StateSchema(graph)

        assert schema.name == "linear_graph"

    def test_input_ref_created(self):
        """Test that input refs are created correctly."""
        with GraphNode(name="linear_graph") as graph:
            node_a = CodeNode(
                name="node_a",
                code_fn=lambda x: {"result": x + 10},
                inputs={"x": PARENT["x"]}
            )
            START >> node_a >> END

        graph.build()
        schema = StateSchema(graph)

        # node_a.x should ref to linear_graph.x
        idx = schema.get_index("linear_graph.node_a", "x")
        assert isinstance(schema._pull_refs[idx], Ref)

    def test_output_ref_created(self):
        """Test that output refs are created correctly."""
        with GraphNode(name="linear_graph") as graph:
            node_a = CodeNode(
                name="node_a",
                code_fn=lambda x: {"result": x + 10},
                inputs={"x": PARENT["x"]},
                outputs={"result": PARENT["result"]}
            )
            START >> node_a >> END

        graph.build()
        schema = StateSchema(graph)

        # node_a.result should be push ref to linear_graph.result
        idx = schema.get_index("linear_graph.node_a", "result")
        ref = schema.get_push_ref(idx)
        assert isinstance(ref, Ref)
        assert ref.is_output is True
        assert ref.node == "linear_graph"
        assert ref.var == "result"

    def test_unique_indices(self):
        """Test that all indices are unique."""
        with GraphNode(name="test_graph") as graph:
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

        all_indices = [schema.get_index(n, v) for n, v in schema]
        assert len(all_indices) == len(set(all_indices))


# ============================================================
# Test 2: Ref with Operations
# ============================================================

class TestRefWithOperations:
    """Test refs with chained operations."""

    def test_getitem_operations(self):
        """Test ref with getitem operations."""
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
            START >> node_a >> node_b >> END

        graph.build()
        schema = StateSchema(graph)

        # Test the fn extracts items correctly
        items_idx = schema.get_index("ref_ops_graph.extract_items", "items")
        test_data = {"items": [1, 2, 3], "name": "hello"}
        assert schema._pull_refs[items_idx]._fn(test_data) == [1, 2, 3]

    def test_method_call_operations(self):
        """Test ref with method call operations."""
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
            START >> node_a >> node_b >> END

        graph.build()
        schema = StateSchema(graph)

        # Test the fn applies upper() correctly
        name_idx = schema.get_index("ref_ops_graph.transform_name", "name")
        test_data = {"name": "hello"}
        assert schema._pull_refs[name_idx]._fn(test_data) == "HELLO"


# ============================================================
# Test 3: Ref with Apply
# ============================================================

class TestRefWithApply:
    """Test refs with apply() function."""

    def test_apply_len(self):
        """Test ref with apply(len)."""
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
            START >> node_a >> node_b >> END

        graph.build()
        schema = StateSchema(graph)

        test_numbers = [5, 2, 8, 1, 9, 3]
        length_idx = schema.get_index("ref_apply_graph.get_length", "length")
        assert schema._pull_refs[length_idx]._fn(test_numbers) == 6

    def test_apply_sorted(self):
        """Test ref with apply(sorted)."""
        with GraphNode(name="ref_apply_graph") as graph:
            node_a = CodeNode(
                name="list_source",
                code_fn=lambda: {"numbers": [5, 2, 8]},
                inputs={}
            )
            node_b = CodeNode(
                name="sort_numbers",
                code_fn=lambda sorted_nums: {"sorted": sorted_nums},
                inputs={"sorted_nums": node_a["numbers"].apply(sorted)}
            )
            START >> node_a >> node_b >> END

        graph.build()
        schema = StateSchema(graph)

        test_numbers = [5, 2, 8, 1, 9, 3]
        sorted_idx = schema.get_index("ref_apply_graph.sort_numbers", "sorted_nums")
        assert schema._pull_refs[sorted_idx]._fn(test_numbers) == [1, 2, 3, 5, 8, 9]


# ============================================================
# Test 4: Arithmetic Operations
# ============================================================

class TestArithmeticOperations:
    """Test refs with arithmetic operations."""

    def test_add_and_multiply(self):
        """Test ref with (value + 5) * 2."""
        with GraphNode(name="arithmetic_graph") as graph:
            node_a = CodeNode(
                name="number_source",
                code_fn=lambda: {"value": 10},
                inputs={}
            )
            node_b = CodeNode(
                name="compute",
                code_fn=lambda x: {"result": x},
                inputs={"x": (node_a["value"] + 5) * 2}
            )
            START >> node_a >> node_b >> END

        graph.build()
        schema = StateSchema(graph)

        x_idx = schema.get_index("arithmetic_graph.compute", "x")
        assert schema._pull_refs[x_idx]._fn(10) == 30  # (10 + 5) * 2


# ============================================================
# Test 5: Nested Graph
# ============================================================

class TestNestedGraph:
    """Test schema with nested graphs."""

    def test_nested_refs(self):
        """Test refs in nested graph structure."""
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

        # outer.inner.x should ref to outer.x
        assert ("outer", "x") in schema
        assert isinstance(schema._pull_refs[schema.get_index("outer.inner", "x")], Ref)

        # inner.result should be push ref to outer.inner_result
        inner_result_ref = schema.get_push_ref(schema.get_index("outer.inner", "result"))
        assert isinstance(inner_result_ref, Ref)
        assert inner_result_ref.is_output is True


# ============================================================
# Test 6: Deeply Nested Graphs
# ============================================================

class TestDeeplyNestedGraphs:
    """Test schema with 3-level nesting."""

    def test_three_level_nesting(self):
        """Test refs chain through 3 nested levels."""
        with GraphNode(name="level1") as level1:
            with GraphNode(
                name="level2",
                inputs={"x": PARENT["x"]},
                outputs={"result": PARENT["result"]}
            ) as level2:
                with GraphNode(
                    name="level3",
                    inputs={"x": PARENT["x"]},
                    outputs={"result": PARENT["result"]}
                ) as level3:
                    core = CodeNode(
                        name="core",
                        code_fn=lambda x: {"result": x * 3},
                        inputs={"x": PARENT["x"]},
                        outputs={"result": PARENT["result"]}
                    )
                    START >> core >> END
                START >> level3 >> END
            START >> level2 >> END

        level1.build()
        schema = StateSchema(level1)

        # Verify deep nesting refs
        assert isinstance(schema._pull_refs[schema.get_index("level1.level2", "x")], Ref)
        assert isinstance(schema._pull_refs[schema.get_index("level1.level2.level3", "x")], Ref)
        assert isinstance(schema._pull_refs[schema.get_index("level1.level2.level3.core", "x")], Ref)


# ============================================================
# Test 7: Iteration Node
# ============================================================

class TestIterationNode:
    """Test schema with iteration nodes."""

    def test_while_loop_refs(self):
        """Test refs in WhileLoopNode.

        Note: After refactor, iteration nodes inherit directly from GraphNode
        (no inner graph), so child nodes reference the loop node directly.
        """
        from hush.core.nodes.iteration.while_loop_node import WhileLoopNode

        with WhileLoopNode(
            name="counter_loop",
            inputs={"counter": 0},
            stop_condition="counter >= 5",
            max_iterations=10
        ) as loop:
            inc = CodeNode(
                name="increment",
                code_fn=lambda counter: {"new_counter": counter + 1},
                inputs={"counter": PARENT["counter"]},
                outputs={"new_counter": PARENT["counter"]}
            )
            START >> inc >> END

        loop.build()
        schema = StateSchema(loop)

        # Child node's counter should ref to loop.counter (PARENT resolution)
        # Iteration nodes now inherit from GraphNode directly, no __inner__ graph
        child_counter_idx = schema.get_index("counter_loop.increment", "counter")
        child_counter_ref = schema._pull_refs[child_counter_idx]
        assert child_counter_ref is not None
        assert child_counter_ref.node == "counter_loop"
        assert child_counter_ref.var == "counter"


# ============================================================
# Test 8: Collection Interface
# ============================================================

class TestCollectionInterface:
    """Test schema collection interface (__iter__, __len__, __contains__)."""

    def test_iteration(self):
        """Test iterating over schema."""
        with GraphNode(name="test_graph") as graph:
            node = CodeNode(
                name="node",
                code_fn=lambda x: {"y": x},
                inputs={"x": PARENT["x"]}
            )
            START >> node >> END

        graph.build()
        schema = StateSchema(graph)

        keys = list(schema)
        assert len(keys) > 0
        assert all(isinstance(k, tuple) and len(k) == 2 for k in keys)

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

        assert ("test_graph", "x") in schema
        assert ("test_graph.node", "x") in schema
        assert ("nonexistent", "var") not in schema

    def test_len(self):
        """Test __len__ method."""
        with GraphNode(name="test_graph") as graph:
            node = CodeNode(
                name="node",
                code_fn=lambda x: {"y": x},
                inputs={"x": PARENT["x"]}
            )
            START >> node >> END

        graph.build()
        schema = StateSchema(graph)

        assert len(schema) > 0
        assert len(schema) == schema.num_indices
