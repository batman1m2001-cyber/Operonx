"""Tests for auto_name module: variable name extraction, frame skipping, bytecode analysis."""

from hush.core.nodes.base import PARENT, BaseNode
from hush.core.nodes.flow.branch_node import Branch, BranchNode
from hush.core.nodes.graph.graph_node import GraphNode
from hush.core.nodes.iteration.base import Each
from hush.core.nodes.iteration.for_loop_node import ForLoopNode
from hush.core.nodes.iteration.map_node import MapNode
from hush.core.nodes.transform.code_node import CodeNode, code_node
from hush.core.nodes.transform.parser_node import ParserNode
from hush.core.utils.auto_name import (
    _name_from_bytecode,
    _parse_assignment,
    _skip_code_objects,
    auto_name,
    register_skip,
    unique_name,
)


class TestParseAssignment:
    """Unit tests for _parse_assignment() pure function."""

    def test_simple_assignment(self):
        assert _parse_assignment("x = foo()") == "x"

    def test_underscore_name(self):
        assert _parse_assignment("my_var = bar()") == "my_var"

    def test_annotated_assignment(self):
        assert _parse_assignment("x: int = foo()") == "x"

    def test_annotated_no_value_rejected(self):
        """Annotation without value (x: int) should not match."""
        assert _parse_assignment("x: int") is None

    def test_comparison_equals_rejected(self):
        assert _parse_assignment("x == 5") is None

    def test_comparison_gte_rejected(self):
        assert _parse_assignment("x >= 5") is None

    def test_comparison_ne_rejected(self):
        assert _parse_assignment("x != 5") is None

    def test_augmented_assignment_rejected(self):
        assert _parse_assignment("x += 1") is None

    def test_multi_target_rejected(self):
        """a = b = foo() has 2 targets, should be rejected."""
        assert _parse_assignment("a = b = foo()") is None

    def test_tuple_unpack_rejected(self):
        """a, b = foo() has tuple target, not Name."""
        assert _parse_assignment("a, b = foo()") is None

    def test_attribute_assignment_rejected(self):
        """self.x = foo() should not return 'self'."""
        assert _parse_assignment("self.x = foo()") is None

    def test_subscript_assignment_rejected(self):
        """d['x'] = foo() should not return anything."""
        assert _parse_assignment("d['x'] = foo()") is None

    def test_multiline_regex_fallback(self):
        """Incomplete line like 'x = (' triggers SyntaxError, falls back to regex."""
        assert _parse_assignment("x = (") == "x"

    def test_empty_line(self):
        assert _parse_assignment("") is None

    def test_function_call_only(self):
        assert _parse_assignment("foo()") is None

    def test_expression_statement(self):
        assert _parse_assignment("print(x)") is None


class TestRegisterSkip:
    """Tests for register_skip() and the code object registry."""

    def test_register_returns_function(self):
        def my_fn():
            pass

        result = register_skip(my_fn)
        assert result is my_fn

    def test_registered_code_in_set(self):
        def my_fn2():
            pass

        register_skip(my_fn2)
        assert my_fn2.__code__ in _skip_code_objects

    def test_as_decorator(self):
        @register_skip
        def my_fn3():
            pass

        assert my_fn3.__code__ in _skip_code_objects

    def test_idempotent(self):
        def my_fn4():
            pass

        register_skip(my_fn4)
        register_skip(my_fn4)
        # Set deduplicates, no error


class TestUniqueName:
    """Tests for unique_name()."""

    def test_returns_string(self):
        assert isinstance(unique_name(), str)

    def test_length_8(self):
        assert len(unique_name()) == 8

    def test_unique(self):
        names = {unique_name() for _ in range(100)}
        assert len(names) == 100


class TestAutoName:
    """Integration tests for auto_name() via node creation."""

    def test_base_node_auto_name(self):
        my_node = BaseNode()
        assert my_node.name == "my_node"

    def test_graph_node_auto_name(self):
        graph = GraphNode()
        assert graph.name == "graph"

    def test_code_node_auto_name(self):
        processor = CodeNode(code_fn=lambda: None)
        assert processor.name == "processor"

    def test_parser_node_auto_name(self):
        parser = ParserNode(format="json", extract=["x"])
        assert parser.name == "parser"

    def test_map_node_auto_name(self):
        """MapNode has depth 4 (MapNode → BaseIterationNode → GraphNode → BaseNode)."""
        mapper = MapNode()
        assert mapper.name == "mapper"

    def test_for_loop_node_auto_name(self):
        """ForLoopNode inherits BaseIterationNode.__init__ (depth 3)."""
        loop = ForLoopNode()
        assert loop.name == "loop"

    def test_branch_node_auto_name(self):
        router = BranchNode()
        assert router.name == "router"

    def test_explicit_name_preserved(self):
        node = BaseNode(name="explicit")
        assert node.name == "explicit"

    def test_no_assignment_falls_back(self):
        """When created inside a data structure, bytecode sees the container's STORE.

        Bytecode picks up 'nodes' because the list assignment `nodes = [...]`
        results in STORE_FAST for 'nodes'. This is expected behavior — bytecode
        is more capable than source parsing.
        """
        nodes = [BaseNode()]
        assert nodes[0].name is not None
        assert isinstance(nodes[0].name, str)


class TestShorthandAutoName:
    """Test auto-naming through @shorthand decorated .of() methods."""

    def test_for_loop_of(self):
        my_loop = ForLoopNode.of(value=Each([1, 2, 3]))
        assert my_loop.name == "my_loop"

    def test_map_of(self):
        my_map = MapNode.of(value=Each([1, 2, 3]))
        assert my_map.name == "my_map"


class TestCodeNodeAutoName:
    """Test auto-naming through @code_node decorator."""

    def test_code_node_decorator(self):
        @code_node
        def greet(person: str):
            return {"greeting": f"Hello, {person}!"}

        g = greet(person="world")
        assert g.name == "g"


class TestBranchAutoName:
    """Test auto-naming through Branch builder (multi-line assignment)."""

    def test_if_else_auto_name(self):
        router = Branch().if_(PARENT["x"] > 0, "pos").else_("neg")
        assert router.name == "router"

    def test_if_build_auto_name(self):
        checker = Branch().if_(PARENT["x"] > 0, "process").build()
        assert checker.name == "checker"
