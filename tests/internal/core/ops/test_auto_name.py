"""Tests for auto_name module: variable name extraction, frame skipping, bytecode analysis."""

from operonx.core.ops.base import PARENT, BaseOp
from operonx.core.ops.flow.branch_op import Branch, BranchOp
from operonx.core.ops.graph.graph_op import GraphOp
from operonx.core.ops.transform.func_op import FuncOp, op
from operonx.core.utils.auto_name import (
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
        my_node = BaseOp()
        assert my_node.name == "my_node"

    def test_graph_op_auto_name(self):
        graph = GraphOp()
        assert graph.name == "graph"

    def test_func_op_auto_name(self):
        processor = FuncOp(code_fn=lambda: None)
        assert processor.name == "processor"

    def test_branch_op_auto_name(self):
        router = BranchOp()
        assert router.name == "router"

    def test_explicit_name_preserved(self):
        node = BaseOp(name="explicit")
        assert node.name == "explicit"

    def test_no_assignment_falls_back(self):
        """When created inside a data structure, bytecode sees the container's STORE.

        Bytecode picks up 'nodes' because the list assignment `nodes = [...]`
        results in STORE_FAST for 'nodes'. This is expected behavior — bytecode
        is more capable than source parsing.
        """
        nodes = [BaseOp()]
        assert nodes[0].name is not None
        assert isinstance(nodes[0].name, str)


class TestFuncOpAutoName:
    """Test auto-naming through @op decorator."""

    def test_func_op_decorator(self):
        @op
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
