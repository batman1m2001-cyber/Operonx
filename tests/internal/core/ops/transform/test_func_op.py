"""Tests for FuncOp - Python code execution node."""

import pytest

from operonx.core.ops.transform.func_op import FuncOp, op

# ============================================================
# Test Fixtures
# ============================================================


@pytest.fixture
def add_numbers_node():
    """Create an add_numbers node for testing."""

    @op
    def add_numbers(a: int, b: int = 10):
        """Add two numbers."""
        return {"result": a + b}

    return add_numbers()


@pytest.fixture
def process_data_node():
    """Create a process_data node for testing."""

    @op
    def process_data(name: str, count: int):
        """Process data with type hints and descriptions."""
        return {
            "message": f"Hello, {name}!",
            "total": count * 2,
            "status": "success",
        }

    return process_data()


@pytest.fixture
def increment_node():
    """Create an increment node for testing."""

    @op
    def increment(x: int):
        return {"x": x + 1}

    return increment()


# ============================================================
# Test 1: Basic __call__ Usage
# ============================================================


class TestBasicCallUsage:
    """Test direct __call__ invocation of FuncOp."""

    def test_add_numbers_basic(self, add_numbers_node):
        """Test basic addition with two arguments."""
        result = add_numbers_node(a=10, b=20)
        assert result == {"result": 30}

    def test_add_numbers_different_values(self, add_numbers_node):
        """Test addition with different values."""
        result = add_numbers_node(a=5, b=3)
        assert result == {"result": 8}

    def test_add_numbers_with_default(self, add_numbers_node):
        """Test that default parameter value is used."""
        result = add_numbers_node(a=7)
        assert result == {"result": 17}  # 7 + default 10 = 17


# ============================================================
# Test 2: Increment Function
# ============================================================


class TestIncrementFunction:
    """Test increment function behavior."""

    def test_increment_positive(self, increment_node):
        """Test increment with positive number."""
        result = increment_node(x=5)
        assert result == {"x": 6}

    def test_increment_zero(self, increment_node):
        """Test increment from zero."""
        result = increment_node(x=0)
        assert result == {"x": 1}

    def test_increment_negative(self, increment_node):
        """Test increment with negative number."""
        result = increment_node(x=-1)
        assert result == {"x": 0}


# ============================================================
# Test 3: Multiple Outputs
# ============================================================


class TestMultipleOutputs:
    """Test nodes with multiple output values."""

    def test_process_data_basic(self, process_data_node):
        """Test process_data with basic inputs."""
        result = process_data_node(name="World", count=5)
        assert result["message"] == "Hello, World!"
        assert result["total"] == 10
        assert result["status"] == "success"

    def test_process_data_different_values(self, process_data_node):
        """Test process_data with different values."""
        result = process_data_node(name="Alice", count=3)
        assert result["message"] == "Hello, Alice!"
        assert result["total"] == 6


# ============================================================
# Test 4: Schema Extraction
# ============================================================


class TestSchemaExtraction:
    """Test automatic schema extraction from function signature."""

    def test_add_numbers_inputs_schema(self, add_numbers_node):
        """Test that inputs are correctly extracted."""
        assert "a" in add_numbers_node.inputs
        assert "b" in add_numbers_node.inputs

    def test_add_numbers_outputs_schema(self, add_numbers_node):
        """Test that outputs are correctly extracted."""
        assert "result" in add_numbers_node.outputs

    def test_required_parameter(self, add_numbers_node):
        """Test that required parameters are marked correctly."""
        assert add_numbers_node.inputs["a"].required is True

    def test_default_parameter_value(self, add_numbers_node):
        """Test that default values are captured."""
        assert add_numbers_node.inputs["b"].default == 10

    def test_process_data_outputs(self, process_data_node):
        """Test that all outputs are extracted from return dict."""
        assert "message" in process_data_node.outputs
        assert "total" in process_data_node.outputs
        assert "status" in process_data_node.outputs


# ============================================================
# Test 5: Async Functions
# ============================================================


class TestAsyncFunctions:
    """Test async function handling."""

    @pytest.mark.asyncio
    async def test_async_func_op(self):
        """Test that async functions work correctly."""

        @op
        async def async_double(x: int):
            return {"result": x * 2}

        node = async_double()
        # Note: __call__ handles async internally
        result = node(x=5)
        assert result == {"result": 10}


# ============================================================
# Test 6: Code Node Decorator
# ============================================================


class TestFuncOpDecorator:
    """Test @op decorator behavior."""

    def test_decorator_creates_factory(self):
        """Test that decorator creates a factory function."""

        @op
        def my_func(x: int):
            return {"y": x}

        # my_func should now be a factory
        node = my_func()
        assert isinstance(node, FuncOp)

    def test_decorator_uses_variable_name(self):
        """Test that node name comes from variable assignment, not function name."""

        @op
        def some_function(x: int):
            return {"y": x}

        my_node = some_function()
        assert my_node.name == "my_node"

    def test_decorator_custom_name(self):
        """Test that custom name can be provided."""

        @op
        def original_name(x: int):
            return {"y": x}

        node = original_name(name="custom")
        assert node.name == "custom"


# ============================================================
# Test 7: Flat Kwargs Syntax via @op
# ============================================================


class TestFlatKwargsSyntax:
    """Test flat kwargs syntax: my_func(x=value, y=value)."""

    def test_flat_input_mapping(self):
        """Test that flat kwargs map to inputs."""

        @op
        def add(a: int, b: int):
            return {"result": a + b}

        node = add(a="some_ref", b="other_ref")
        assert node.inputs["a"].value == "some_ref"
        assert node.inputs["b"].value == "other_ref"

    def test_flat_output_mapping(self):
        """Test that flat kwargs map to outputs."""

        @op
        def compute(x: int):
            return {"result": x * 2}

        node = compute(result="mapped_output")
        assert node.outputs["result"].value == "mapped_output"

    def test_flat_mixed_input_output(self):
        """Test mixing input and output keys in flat kwargs."""

        @op
        def transform(x: int):
            return {"y": x + 1}

        node = transform(x="input_ref", y="output_ref")
        assert node.inputs["x"].value == "input_ref"
        assert node.outputs["y"].value == "output_ref"

    def test_flat_unknown_key_raises(self):
        """Test that unknown keys raise TypeError."""

        @op
        def simple(x: int):
            return {"y": x}

        with pytest.raises(TypeError, match="'z' is not a known input or output"):
            simple(z="bad_ref")

    def test_flat_kwargs_with_name(self):
        """Test flat kwargs combined with name kwarg."""

        @op
        def my_func(x: int):
            return {"y": x}

        node = my_func(name="custom", x="ref")
        assert node.name == "custom"
        assert node.inputs["x"].value == "ref"
