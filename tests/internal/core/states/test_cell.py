"""Tests for Cell - multi-context value storage."""

from operonx.core.states.cell import DEFAULT_CONTEXT, Cell

# ============================================================
# Test 1: Basic Cell Operations
# ============================================================


class TestBasicCellOperations:
    """Test basic Cell set/get operations."""

    def test_set_and_get_with_context(self):
        """Test setting and getting values with specific contexts."""
        cell = Cell(default_value=0)

        cell["loop1"] = 10
        cell["loop2"] = 20

        assert cell["loop1"] == 10
        assert cell["loop2"] == 20

    def test_none_context_uses_default(self):
        """Test that None context maps to DEFAULT_CONTEXT."""
        cell = Cell(default_value=0)

        cell[None] = 30

        assert cell[None] == 30
        assert cell[DEFAULT_CONTEXT] == 30

    def test_default_value_returned_for_missing_context(self):
        """Test that default_value is returned for missing contexts."""
        cell = Cell(default_value=42)

        assert cell["nonexistent"] == 42


# ============================================================
# Test 2: Context Storage
# ============================================================


class TestContextStorage:
    """Test Cell context storage."""

    def test_contexts_stores_values(self):
        """Test that contexts dict stores all values."""
        cell = Cell()

        cell["first"] = 1
        cell["second"] = 2
        cell["third"] = 3

        assert len(cell.contexts) == 3
        assert cell.contexts["first"] == 1
        assert cell.contexts["second"] == 2
        assert cell.contexts["third"] == 3

    def test_updating_existing_context(self):
        """Test that updating existing context overwrites value."""
        cell = Cell()

        cell["ctx1"] = 1
        cell["ctx2"] = 2
        cell["ctx1"] = 10  # Update existing

        assert len(cell.contexts) == 2
        assert cell["ctx1"] == 10


# ============================================================
# Test 3: Contains
# ============================================================


class TestContains:
    """Test __contains__ method."""

    def test_contains_returns_true_for_existing(self):
        """Test 'in' operator for existing contexts."""
        cell = Cell()
        cell["existing"] = 1

        assert "existing" in cell

    def test_contains_returns_false_for_missing(self):
        """Test 'in' operator for missing contexts."""
        cell = Cell()

        assert "missing" not in cell


# ============================================================
# Test 4: Repr
# ============================================================


class TestRepr:
    """Test __repr__ method."""

    def test_repr_shows_context_count(self):
        """Test repr shows context count and default."""
        cell = Cell(default_value=0)
        cell["ctx1"] = 10
        cell["ctx2"] = 20

        repr_str = repr(cell)

        assert "Cell" in repr_str
        assert "2" in repr_str  # Context count
