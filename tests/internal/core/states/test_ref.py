"""Tests for Ref - variable reference with chainable operations."""

import pytest

from operonx.core.states.ref import Ref

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
        assert ref.has_transforms is False

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

        assert ref.has_transforms is False
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
        assert ref_clone.transforms == ref.transforms

    def test_clone_executes_same(self):
        """Test clone produces same result."""
        ref = Ref("n", "x")["key"] + 5
        ref_clone = ref._clone()

        assert ref_clone.execute({"key": 10}) == ref.execute({"key": 10})


# ============================================================
# Test 14: Deserialization
# ============================================================


class TestDeserialization:
    """Test rebuilding Ref from transforms."""

    def test_rebuild_from_transforms(self):
        """Test Ref can be rebuilt from serialized transforms."""
        transforms = [("getitem", ("key",)), ("add", (5,))]
        ref = Ref("n", "x", _transforms=transforms)

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

    def test_repr_with_transforms(self):
        """Test repr shows transforms count."""
        ref = Ref("graph.node", "out")["key"] + 5
        assert "transforms=2" in repr(ref)


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

    def test_ref_with_transforms(self):
        """Test get_all_vars for ref with transforms (no compound)."""
        ref = Ref("n", "a")
        ref_with_transforms = ref["key"] + 5 > 10
        assert ref_with_transforms.get_all_vars() == {"a"}

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


# ============================================================
# Test 20: Ref operands (S7) — a Ref on the other side is read, not captured
# ============================================================


class TestRefOperands:
    """``a >= b`` with two Refs compares two values, not a value and a Ref.

    Only ``&``/``|`` used to resolve a Ref operand; every other operator
    captured it as a literal, so ``a >= b`` built one more Ref — truthy,
    so a branch on it took the first case every time.
    """

    CTX = {("n", "a"): 7, ("n", "b"): 2}

    @pytest.mark.parametrize(
        "build,want",
        [
            pytest.param(lambda a, b: a < b, False, id="lt"),
            pytest.param(lambda a, b: a <= b, False, id="le"),
            pytest.param(lambda a, b: a > b, True, id="gt"),
            pytest.param(lambda a, b: a >= b, True, id="ge"),
            pytest.param(lambda a, b: a == b, False, id="eq"),
            pytest.param(lambda a, b: a != b, True, id="ne"),
            pytest.param(lambda a, b: a + b, 9, id="add"),
            pytest.param(lambda a, b: a - b, 5, id="sub"),
            pytest.param(lambda a, b: b - a, -5, id="sub-other-way"),
            pytest.param(lambda a, b: a * b, 14, id="mul"),
            pytest.param(lambda a, b: a / b, 3.5, id="truediv"),
            pytest.param(lambda a, b: a // b, 3, id="floordiv"),
            pytest.param(lambda a, b: a % b, 1, id="mod"),
            pytest.param(lambda a, b: a**b, 49, id="pow"),
        ],
    )
    def test_the_other_ref_is_resolved(self, build, want):
        a, b = Ref("n", "a"), Ref("n", "b")
        expr = build(a, b)
        got = expr.execute(self.CTX[("n", expr.var)], self.CTX)
        assert not isinstance(got, Ref), "the other side was captured as a Ref"
        assert got == want

    def test_contains_a_ref(self):
        items, item = Ref("n", "items"), Ref("n", "item")
        expr = items.__contains__(item)
        ctx = {("n", "items"): [1, 2], ("n", "item"): 2}
        assert expr.execute([1, 2], ctx) is True
        ctx[("n", "item")] = 3
        assert expr.execute([1, 2], ctx) is False

    def test_getitem_with_a_ref_key(self):
        data, key = Ref("n", "data"), Ref("n", "key")
        expr = data[key]
        ctx = {("n", "data"): {"x": 1, "y": 2}, ("n", "key"): "y"}
        assert expr.execute({"x": 1, "y": 2}, ctx) == 2

    def test_a_chain_on_both_sides(self):
        a, b = Ref("n", "a"), Ref("n", "b")
        expr = (a + 1) > (b * 10)
        assert expr.execute(7, self.CTX) is False
        assert expr.execute(30, {**self.CTX, ("n", "a"): 30}) is True

    def test_get_all_vars_includes_every_operand(self):
        a, b, c = Ref("n", "a"), Ref("n", "b"), Ref("n", "c")
        assert (a >= b).get_all_vars() == {"a", "b"}
        assert ((a + b) > c).get_all_vars() == {"a", "b", "c"}
        assert (a[b] == c).get_all_vars() == {"a", "b", "c"}

    def test_describe_names_the_other_ref(self):
        a, b = Ref("n", "a"), Ref("n", "b")
        assert (a >= b).describe() == "a >= b"


# ============================================================
# Test 21: Resolution keyed by source (S8)
# ============================================================


class TestResolveBySource:
    """Two Refs with one variable name but different sources stay apart."""

    def test_same_var_from_two_sources(self):
        x, y = Ref("x", "n"), Ref("y", "n")
        cond = (x > 5) & (y < 3)
        assert cond.execute(10, {("x", "n"): 10, ("y", "n"): 1}) is True
        assert cond.execute(10, {("x", "n"): 10, ("y", "n"): 10}) is False

    def test_same_var_compared_across_sources(self):
        x, y = Ref("x", "n"), Ref("y", "n")
        assert (x > y).execute(10, {("x", "n"): 10, ("y", "n"): 1}) is True
        assert (x > y).execute(1, {("x", "n"): 1, ("y", "n"): 10}) is False

    def test_op_sources_are_told_apart_by_identity(self):
        """Two ops may share a display name across graphs; the op is the key."""
        op_a, op_b = MockOp("g.p"), MockOp("g.p")
        cond = (Ref(op_a, "n") > 5) & (Ref(op_b, "n") < 3)
        assert cond.execute(10, {(op_a, "n"): 10, (op_b, "n"): 1}) is True

    def test_get_all_refs_keeps_one_per_source(self):
        x, y = Ref("x", "n"), Ref("y", "n")
        cond = (x > 5) & (y < 3) & (x < 100)
        assert [(r.raw_source, r.var) for r in cond.get_all_refs()] == [("x", "n"), ("y", "n")]
        assert all(not r.has_transforms for r in cond.get_all_refs())


# ============================================================
# Test 22: Introspection (S9) — probing a private name builds nothing
# ============================================================


class TestIntrospection:
    """``hasattr`` on a Ref must answer, not fabricate.

    ``ref.field`` is DSL and builds a ``getattr`` transform, but a
    ``_``-prefixed or dunder name is never a field: debuggers, copy,
    pickle, pydantic and numpy probe those, and each probe used to be
    at risk of building a Ref.
    """

    PROBES = [
        "_private",
        "__len__",
        "__copy__",
        "__deepcopy__",
        "__getstate__",
        "__setstate__",
        "__fspath__",
        "__array__",
        "__html__",
        "_repr_html_",
        "__dataclass_fields__",
        "__get_pydantic_core_schema__",
    ]

    @pytest.mark.parametrize("name", PROBES)
    def test_a_private_name_is_absent(self, name):
        assert hasattr(Ref("n", "x"), name) is False

    def test_probing_builds_no_ref(self, monkeypatch):
        built = []
        monkeypatch.setattr(Ref, "_with_transform", lambda self, *a: built.append(a))
        for name in self.PROBES:
            hasattr(Ref("n", "x"), name)
        assert built == []

    def test_a_public_name_is_still_a_field(self):
        from types import SimpleNamespace

        field = Ref("n", "obj").some_field
        assert isinstance(field, Ref)
        assert field.transforms == [("getattr", ("some_field",))]
        assert field.execute(SimpleNamespace(some_field=3)) == 3

    def test_copy_and_deepcopy(self):
        import copy

        ref = Ref("n", "x")["k"] > 3
        for dup in (copy.copy(ref), copy.deepcopy(ref)):
            assert isinstance(dup, Ref)
            assert dup.transforms == ref.transforms
            assert dup.execute({"k": 5}) is True


class TestNotIterable:
    """``Ref.__getitem__`` made a Ref iterable by Python's legacy protocol:
    ``ref[0]``, ``ref[1]``, … each a new Ref, never an ``IndexError``. So
    ``list(ref)`` — or any tool that iterates what it is handed — never
    returned."""

    def test_iter_refuses(self):
        with pytest.raises(TypeError, match="not iterable"):
            iter(Ref("n", "x"))

    def test_unpacking_refuses(self):
        with pytest.raises(TypeError, match="not iterable"):
            first, second = Ref("n", "pair")

    def test_it_is_not_an_iterable(self):
        from collections.abc import Iterable

        assert not isinstance(Ref("n", "x"), Iterable)

    def test_indexing_is_still_dsl(self):
        assert Ref("n", "items")[0].execute(["a", "b"]) == "a"


# ============================================================
# Test 23: No truthiness (E9) — `and` / `or` / `not` cannot see a Ref
# ============================================================


class TestNoTruthiness:
    """``x == 1 and y == 2`` asks Python for ``bool(x == 1)``, which a Ref
    cannot answer at build time. It used to say True, so the expression
    silently became ``y == 2``. Now it refuses and names the operators
    that do work."""

    def _refs(self):
        return Ref("n", "x"), Ref("n", "y")

    def test_bool_refuses(self):
        x, _ = self._refs()
        with pytest.raises(TypeError, match=r"&.*\|.*~"):
            bool(x == 1)

    def test_and_refuses(self):
        x, y = self._refs()
        with pytest.raises(TypeError, match="&"):
            _ = x == 1 and y == 2

    def test_or_refuses(self):
        x, y = self._refs()
        with pytest.raises(TypeError, match=r"\|"):
            _ = x == 1 or y == 2

    def test_not_refuses(self):
        x, _ = self._refs()
        with pytest.raises(TypeError, match="~"):
            _ = not x

    def test_in_refuses(self):
        """``v in ref`` coerces to bool too; it used to be always True."""
        x, _ = self._refs()
        with pytest.raises(TypeError):
            _ = 1 in x

    def test_the_operators_still_combine(self):
        x, y = self._refs()
        cond = ((x == 1) & (y == 2)) | ~(x == 0)
        assert cond.execute(1, {("n", "x"): 1, ("n", "y"): 2}) is True
        assert cond.execute(0, {("n", "x"): 0, ("n", "y"): 3}) is False
