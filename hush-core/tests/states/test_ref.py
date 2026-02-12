"""Tests for Ref - variable reference with chainable operations."""

import pytest

from hush.core.states.ref import Ref

# ============================================================
# Helper Classes
# ============================================================


class MockOp:
    """Mock node for testing."""

    def __init__(self, name: str):
        self.full_name = name


# ============================================================
# Test 1: Basic Creation
# ============================================================


class TestBasicCreation:
    """Test basic Ref creation."""

    def test_string_node(self):
        """Test Ref creation with string node."""
        ref = Ref("graph.node", "output")

        assert ref.source == "graph.node"
        assert ref.var == "output"
        assert ref.has_ops is False

    def test_mock_op(self):
        """Test Ref creation with MockOp."""
        mock = MockOp("graph.mock")
        ref = Ref(mock, "result")

        assert ref.source == "graph.mock"
        assert ref.raw_source is mock

    def test_as_tuple(self):
        """Test as_tuple method."""
        ref = Ref("graph.node", "output")

        assert ref.as_tuple() == ("graph.node", "output")


# ============================================================
# Test 2: Getitem Operations
# ============================================================


class TestGetitemOperations:
    """Test getitem operations."""

    def test_dict_access(self):
        """Test dict key access."""
        ref = Ref("n", "data")

        assert ref["key"].execute({"key": "value"}) == "value"

    def test_list_access(self):
        """Test list index access."""
        ref = Ref("n", "data")

        assert ref[0].execute([10, 20]) == 10
        assert ref[-1].execute([10, 20, 30]) == 30

    def test_nested_access(self):
        """Test nested dict access."""
        ref = Ref("n", "data")

        assert ref["a"]["b"].execute({"a": {"b": 42}}) == 42

    def test_slice_access(self):
        """Test slice access."""
        ref = Ref("n", "data")

        assert ref[1:3].execute([0, 1, 2, 3]) == [1, 2]


# ============================================================
# Test 3: Getattr Operations
# ============================================================


class TestGetattrOperations:
    """Test getattr operations."""

    def test_simple_attribute(self):
        """Test simple attribute access."""

        class Obj:
            name = "test"
            value = 100

        ref = Ref("n", "obj")

        assert ref.name.execute(Obj()) == "test"
        assert ref.value.execute(Obj()) == 100


# ============================================================
# Test 4: Method Call Operations
# ============================================================


class TestMethodCallOperations:
    """Test method call operations."""

    def test_upper(self):
        """Test string upper() method."""
        ref = Ref("n", "data")

        assert ref.upper().execute("hello") == "HELLO"

    def test_split(self):
        """Test string split() method."""
        ref = Ref("n", "data")

        assert ref.split(",").execute("a,b,c") == ["a", "b", "c"]

    def test_replace(self):
        """Test string replace() method."""
        ref = Ref("n", "data")

        assert ref.replace("a", "x").execute("banana") == "bxnxnx"

    def test_chained_methods(self):
        """Test chained method calls."""
        ref = Ref("n", "data")

        assert ref.strip().lower().execute("  HELLO  ") == "hello"


# ============================================================
# Test 5: Arithmetic Operations
# ============================================================


class TestArithmeticOperations:
    """Test arithmetic operations."""

    def test_add(self):
        """Test addition."""
        ref = Ref("n", "num")
        assert (ref + 5).execute(10) == 15
        assert (5 + ref).execute(10) == 15

    def test_sub(self):
        """Test subtraction."""
        ref = Ref("n", "num")
        assert (ref - 3).execute(10) == 7
        assert (20 - ref).execute(8) == 12

    def test_mul(self):
        """Test multiplication."""
        ref = Ref("n", "num")
        assert (ref * 4).execute(5) == 20
        assert (4 * ref).execute(5) == 20

    def test_truediv(self):
        """Test true division."""
        ref = Ref("n", "num")
        assert (ref / 2).execute(10) == 5.0
        assert (100 / ref).execute(4) == 25.0

    def test_floordiv(self):
        """Test floor division."""
        ref = Ref("n", "num")
        assert (ref // 3).execute(10) == 3
        assert (10 // ref).execute(3) == 3

    def test_mod(self):
        """Test modulo."""
        ref = Ref("n", "num")
        assert (ref % 3).execute(10) == 1
        assert (10 % ref).execute(3) == 1

    def test_pow(self):
        """Test power."""
        ref = Ref("n", "num")
        assert (ref**2).execute(5) == 25
        assert (2**ref).execute(3) == 8


# ============================================================
# Test 6: Unary Operations
# ============================================================


class TestUnaryOperations:
    """Test unary operations."""

    def test_neg(self):
        """Test negation."""
        ref = Ref("n", "num")
        assert (-ref).execute(5) == -5

    def test_pos(self):
        """Test positive."""
        ref = Ref("n", "num")
        assert (+ref).execute(-5) == -5

    def test_abs(self):
        """Test absolute value."""
        ref = Ref("n", "num")
        assert abs(ref).execute(-5) == 5


# ============================================================
# Test 7: Comparison Operations
# ============================================================


class TestComparisonOperations:
    """Test comparison operations."""

    def test_lt(self):
        """Test less than."""
        ref = Ref("n", "val")
        assert (ref < 10).execute(5) is True
        assert (ref < 10).execute(15) is False

    def test_le(self):
        """Test less than or equal."""
        ref = Ref("n", "val")
        assert (ref <= 10).execute(10) is True

    def test_gt(self):
        """Test greater than."""
        ref = Ref("n", "val")
        assert (ref > 10).execute(15) is True

    def test_ge(self):
        """Test greater than or equal."""
        ref = Ref("n", "val")
        assert (ref >= 10).execute(10) is True

    def test_eq(self):
        """Test equality returns Ref."""
        ref = Ref("n", "val")
        ref_eq = ref == 10
        assert isinstance(ref_eq, Ref)
        assert ref_eq.execute(10) is True

    def test_ne(self):
        """Test not equal."""
        ref = Ref("n", "val")
        assert (ref != 10).execute(5) is True


# ============================================================
# Test 8: Contains Operation
# ============================================================


class TestContainsOperation:
    """Test contains operation."""

    def test_contains_in_list(self):
        """Test contains in list."""
        ref = Ref("n", "container")
        ref_contains = ref.__contains__("x")

        assert ref_contains.execute(["a", "x", "b"]) is True
        assert ref_contains.execute(["a", "b"]) is False

    def test_contains_in_string(self):
        """Test contains in string."""
        ref = Ref("n", "container")
        ref_contains = ref.__contains__("x")

        assert ref_contains.execute("text") is True


# ============================================================
# Test 9: Apply Operation
# ============================================================


class TestApplyOperation:
    """Test apply operation."""

    def test_apply_len(self):
        """Test apply(len)."""
        ref = Ref("n", "data")
        assert ref.apply(len).execute([1, 2, 3]) == 3

    def test_apply_sum(self):
        """Test apply(sum)."""
        ref = Ref("n", "data")
        assert ref.apply(sum).execute([1, 2, 3]) == 6

    def test_apply_sorted(self):
        """Test apply(sorted)."""
        ref = Ref("n", "data")
        assert ref.apply(sorted).execute([3, 1, 2]) == [1, 2, 3]

    def test_apply_with_kwargs(self):
        """Test apply with kwargs."""
        ref = Ref("n", "data")
        assert ref.apply(sorted, reverse=True).execute([3, 1, 2]) == [3, 2, 1]

    def test_apply_lambda(self):
        """Test apply with lambda."""
        ref = Ref("n", "data")
        assert ref.apply(lambda x: x * 2).execute(21) == 42

    def test_apply_chained(self):
        """Test chained getitem then apply."""
        ref = Ref("n", "data")
        assert ref["items"].apply(len).execute({"items": [1, 2, 3]}) == 3


# ============================================================
# Test 10: Complex Chains
# ============================================================


class TestComplexChains:
    """Test complex operation chains."""

    def test_nested_access_and_arithmetic(self):
        """Test nested access followed by arithmetic."""
        ref = Ref("n", "data")
        result = (ref["users"][0]["score"] * 2 + 10).execute({"users": [{"score": 15}]})
        assert result == 40

    def test_division_chain(self):
        """Test chain with division."""
        ref = Ref("n", "data")
        result = ((ref["value"] + 100) / 2).execute({"value": 50})
        assert result == 75.0

    def test_string_concat(self):
        """Test string concatenation."""
        ref = Ref("n", "data")
        result = ("Hello, " + ref["name"] + "!").execute({"name": "World"})
        assert result == "Hello, World!"


# ============================================================
# Test 11: Immutability
# ============================================================


class TestImmutability:
    """Test that operations return new Refs."""

    def test_operations_create_new_refs(self):
        """Test that operations don't mutate original."""
        ref = Ref("n", "x")
        ref_add = ref + 5
        ref_mul = ref * 3

        assert ref.has_ops is False
        assert ref_add is not ref
        assert ref_mul is not ref


# ============================================================
# Test 12: Clone
# ============================================================


class TestClone:
    """Test _clone method."""

    def test_clone_is_new_object(self):
        """Test clone creates new object."""
        ref = Ref("n", "x")["key"] + 5
        ref_clone = ref._clone()

        assert ref_clone is not ref

    def test_clone_preserves_properties(self):
        """Test clone preserves node, ops."""
        ref = Ref("n", "x")["key"] + 5
        ref_clone = ref._clone()

        assert ref_clone.source == ref.source
        assert ref_clone.ops == ref.ops

    def test_clone_executes_same(self):
        """Test clone produces same result."""
        ref = Ref("n", "x")["key"] + 5
        ref_clone = ref._clone()

        assert ref_clone.execute({"key": 10}) == ref.execute({"key": 10})


# ============================================================
# Test 14: Deserialization
# ============================================================


class TestDeserialization:
    """Test rebuilding Ref from ops."""

    def test_rebuild_from_ops(self):
        """Test Ref can be rebuilt from serialized ops."""
        ops = [("getitem", ("key",)), ("add", (5,))]
        ref = Ref("n", "x", _ops=ops)

        assert ref.execute({"key": 10}) == 15


# ============================================================
# Test 15: Error Handling
# ============================================================


class TestErrorHandling:
    """Test error handling."""

    def test_private_attr_raises(self):
        """Test accessing private attr raises AttributeError."""
        ref = Ref("n", "x")

        with pytest.raises(AttributeError):
            _ = ref._private


# ============================================================
# Test 16: Repr
# ============================================================


class TestRepr:
    """Test __repr__ method."""

    def test_repr_basic(self):
        """Test repr for basic ref."""
        ref = Ref("graph.node", "out")
        assert repr(ref) == "Ref('graph.node', 'out')"

    def test_repr_with_ops(self):
        """Test repr shows ops count."""
        ref = Ref("graph.node", "out")["key"] + 5
        assert "ops=2" in repr(ref)


# ============================================================
# Test 17: Boolean Operations with & and |
# ============================================================


class TestBooleanOperations:
    """Test boolean operations with & and |."""

    def test_and_with_literal_true(self):
        """Test & with literal True value."""
        ref = Ref("n", "val")
        result = (ref > 10) & True
        assert result.execute(15, {}) is True
        assert result.execute(5, {}) is False

    def test_and_with_literal_false(self):
        """Test & with literal False value."""
        ref = Ref("n", "val")
        result = (ref > 10) & False
        assert result.execute(15, {}) is False

    def test_and_with_ref_both_true(self):
        """Test & with another Ref, both conditions true."""
        ref_a = Ref("n", "a")
        ref_b = Ref("n", "b")
        compound = (ref_a > 10) & (ref_b == "active")

        ctx = {"a": 15, "b": "active"}
        assert compound.execute(15, ctx) is True

    def test_and_with_ref_first_false(self):
        """Test & with another Ref, first condition false."""
        ref_a = Ref("n", "a")
        ref_b = Ref("n", "b")
        compound = (ref_a > 10) & (ref_b == "active")

        ctx = {"a": 5, "b": "active"}
        assert compound.execute(5, ctx) is False

    def test_and_with_ref_second_false(self):
        """Test & with another Ref, second condition false."""
        ref_a = Ref("n", "a")
        ref_b = Ref("n", "b")
        compound = (ref_a > 10) & (ref_b == "active")

        ctx = {"a": 15, "b": "inactive"}
        assert compound.execute(15, ctx) is False

    def test_or_with_literal(self):
        """Test | with literal value."""
        ref = Ref("n", "val")
        result = (ref > 10) | True
        assert result.execute(5, {}) is True

        result2 = (ref > 10) | False
        assert result2.execute(5, {}) is False
        assert result2.execute(15, {}) is True

    def test_or_with_ref_first_true(self):
        """Test | with another Ref, first condition true."""
        ref_a = Ref("n", "a")
        ref_b = Ref("n", "b")
        compound = (ref_a > 10) | (ref_b == "active")

        ctx = {"a": 15, "b": "inactive"}
        assert compound.execute(15, ctx) is True

    def test_or_with_ref_second_true(self):
        """Test | with another Ref, second condition true."""
        ref_a = Ref("n", "a")
        ref_b = Ref("n", "b")
        compound = (ref_a > 10) | (ref_b == "active")

        ctx = {"a": 5, "b": "active"}
        assert compound.execute(5, ctx) is True

    def test_or_with_ref_both_false(self):
        """Test | with another Ref, both conditions false."""
        ref_a = Ref("n", "a")
        ref_b = Ref("n", "b")
        compound = (ref_a > 10) | (ref_b == "active")

        ctx = {"a": 5, "b": "inactive"}
        assert compound.execute(5, ctx) is False

    def test_not_operator(self):
        """Test ~ (not) operator."""
        ref = Ref("n", "val")
        result = ~(ref > 10)
        assert result.execute(5, {}) is True
        assert result.execute(15, {}) is False

    def test_not_with_equality(self):
        """Test ~ with equality check."""
        ref = Ref("n", "status")
        result = ~(ref == "disabled")
        assert result.execute("active", {}) is True
        assert result.execute("disabled", {}) is False

    def test_complex_and_or_chain(self):
        """Test complex: (a > 10) & (b == 'x') | (c < 5)."""
        ref_a = Ref("n", "a")
        ref_b = Ref("n", "b")
        ref_c = Ref("n", "c")

        # (a > 10) & (b == 'x') | (c < 5)
        # Due to operator precedence: ((a > 10) & (b == 'x')) | (c < 5)
        compound = ((ref_a > 10) & (ref_b == "x")) | (ref_c < 5)

        # First part true: a=15 > 10, b="x" == "x" -> True, short-circuit
        ctx = {"a": 15, "b": "x", "c": 10}
        assert compound.execute(15, ctx) is True

        # First part false, second part true: c=3 < 5 -> True
        ctx = {"a": 5, "b": "y", "c": 3}
        assert compound.execute(5, ctx) is True

        # Both parts false
        ctx = {"a": 5, "b": "y", "c": 10}
        assert compound.execute(5, ctx) is False

    def test_triple_and_chain(self):
        """Test three conditions with &: (a > 0) & (b > 0) & (c > 0)."""
        ref_a = Ref("n", "a")
        ref_b = Ref("n", "b")
        ref_c = Ref("n", "c")

        compound = (ref_a > 0) & (ref_b > 0) & (ref_c > 0)

        # All true
        ctx = {"a": 1, "b": 2, "c": 3}
        assert compound.execute(1, ctx) is True

        # One false
        ctx = {"a": 1, "b": -1, "c": 3}
        assert compound.execute(1, ctx) is False

    def test_triple_or_chain(self):
        """Test three conditions with |: (a > 10) | (b > 10) | (c > 10)."""
        ref_a = Ref("n", "a")
        ref_b = Ref("n", "b")
        ref_c = Ref("n", "c")

        compound = (ref_a > 10) | (ref_b > 10) | (ref_c > 10)

        # First true (short-circuit)
        ctx = {"a": 15, "b": 5, "c": 5}
        assert compound.execute(15, ctx) is True

        # Third true
        ctx = {"a": 5, "b": 5, "c": 15}
        assert compound.execute(5, ctx) is True

        # All false
        ctx = {"a": 5, "b": 5, "c": 5}
        assert compound.execute(5, ctx) is False

    def test_mixed_and_or_not(self):
        """Test mixed: ~(a > 10) & (b == 'active') | (c)."""
        ref_a = Ref("n", "a")
        ref_b = Ref("n", "b")
        ref_c = Ref("n", "c")

        # (~(a > 10) & (b == 'active')) | c
        compound = (~(ref_a > 10) & (ref_b == "active")) | ref_c

        # ~(5 > 10) = True, b == "active" = True -> True
        ctx = {"a": 5, "b": "active", "c": False}
        assert compound.execute(5, ctx) is True

        # ~(15 > 10) = False -> first part false, c = True -> True
        ctx = {"a": 15, "b": "active", "c": True}
        assert compound.execute(15, ctx) is True

        # All false
        ctx = {"a": 15, "b": "inactive", "c": False}
        assert compound.execute(15, ctx) is False


# ============================================================
# Test 18: get_all_vars Method
# ============================================================


class TestGetAllVars:
    """Test get_all_vars method."""

    def test_simple_ref(self):
        """Test get_all_vars for simple ref."""
        ref = Ref("n", "a")
        assert ref.get_all_vars() == {"a"}

    def test_ref_with_ops(self):
        """Test get_all_vars for ref with ops (no compound)."""
        ref = Ref("n", "a")
        ref_with_ops = ref["key"] + 5 > 10
        assert ref_with_ops.get_all_vars() == {"a"}

    def test_and_two_refs(self):
        """Test get_all_vars with & operator."""
        ref_a = Ref("n", "a")
        ref_b = Ref("n", "b")
        compound = (ref_a > 10) & (ref_b == "x")
        assert compound.get_all_vars() == {"a", "b"}

    def test_or_two_refs(self):
        """Test get_all_vars with | operator."""
        ref_a = Ref("n", "a")
        ref_b = Ref("n", "b")
        compound = (ref_a > 10) | (ref_b == "x")
        assert compound.get_all_vars() == {"a", "b"}

    def test_three_refs(self):
        """Test get_all_vars with three refs."""
        ref_a = Ref("n", "a")
        ref_b = Ref("n", "b")
        ref_c = Ref("n", "c")
        compound = (ref_a > 10) & (ref_b == "x") | (ref_c < 5)
        assert compound.get_all_vars() == {"a", "b", "c"}

    def test_four_refs(self):
        """Test get_all_vars with four refs."""
        ref_a = Ref("n", "a")
        ref_b = Ref("n", "b")
        ref_c = Ref("n", "c")
        ref_d = Ref("n", "d")
        compound = (ref_a > 10) & (ref_b == "x") & (ref_c < 5) | (ref_d == True)
        assert compound.get_all_vars() == {"a", "b", "c", "d"}

    def test_not_does_not_add_vars(self):
        """Test that ~ operator doesn't add extra vars."""
        ref = Ref("n", "a")
        compound = ~(ref > 10)
        assert compound.get_all_vars() == {"a"}

    def test_duplicate_var(self):
        """Test that same var used multiple times is deduplicated."""
        ref_a = Ref("n", "a")
        compound = (ref_a > 10) & (ref_a < 100)
        assert compound.get_all_vars() == {"a"}


# ============================================================
# Test 19: Backward Compatibility
# ============================================================


class TestBackwardCompatibility:
    """Test backward compatibility - execute without context."""

    def test_execute_without_context(self):
        """Test execute() still works without context argument."""
        ref = Ref("n", "val")
        assert (ref + 5).execute(10) == 15
        assert (ref * 2).execute(7) == 14

    def test_comparison_without_context(self):
        """Test comparison operations without context."""
        ref = Ref("n", "val")
        assert (ref > 10).execute(15) is True
        assert (ref == "test").execute("test") is True

    def test_getitem_without_context(self):
        """Test getitem without context."""
        ref = Ref("n", "data")
        assert ref["key"].execute({"key": "value"}) == "value"

    def test_apply_without_context(self):
        """Test apply without context."""
        ref = Ref("n", "data")
        assert ref.apply(len).execute([1, 2, 3]) == 3

    def test_chained_ops_without_context(self):
        """Test chained operations without context."""
        ref = Ref("n", "data")
        result = (ref["users"][0]["score"] * 2).execute({"users": [{"score": 15}]})
        assert result == 30
