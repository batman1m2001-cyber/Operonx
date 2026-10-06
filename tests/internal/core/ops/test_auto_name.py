"""Tests for auto_name module: variable name extraction, frame skipping, bytecode analysis."""

from operonx.core.ops.base import PARENT, BaseOp
from operonx.core.ops.flow.branch_op import Branch, BranchOp
from operonx.core.ops.graph.graph_op import GraphOp
from operonx.core.ops.transform.func_op import FuncOp, op
from operonx.core.utils.auto_name import (
    _name_from_bytecode,
    _skip_code_objects,
    auto_name,
    register_skip,
    unique_name,
)


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

    def test_a_tuple_unpack_names_neither_op_on_any_python(self):
        """`a, b = f(), g()`: on 3.11+ the second call is followed by `b`'s
        store, so only it was named — on 3.10 neither was."""
        first, second = BaseOp(), BaseOp()
        assert first.name != "first" and second.name != "second"
        assert first.name != "second" and second.name != "first"

    def test_a_chained_assignment_keeps_its_name(self):
        kept = also = BaseOp()
        assert kept.name == "kept" and also is kept

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


class TestAutoNameCost:
    def test_auto_name_disassembles_once_per_site(self, monkeypatch):
        """The name read off a call site is a fact about that site's bytecode,
        which never changes: an op built in a loop disassembled its caller's
        whole function every time (~1 ms in a large function)."""
        import importlib

        auto_name_module = importlib.import_module("operonx.core.utils.auto_name")

        @op
        def step(x: int):
            return {"y": x}

        reads = []
        original = auto_name_module.dis.get_instructions
        monkeypatch.setattr(
            auto_name_module.dis,
            "get_instructions",
            lambda code: reads.append(code) or original(code),
        )
        names = []
        for i in range(5):
            node = step(x=i)
            names.append(node.name)
        assert names == ["node"] * 5
        assert len(reads) <= 1
