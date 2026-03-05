"""Cell stores multi-context values for workflow state."""

from typing import Any, Dict, Optional

DEFAULT_CONTEXT = ("main",)


class Cell:
    """Stores a variable that may have different values across contexts/iterations.

    Each workflow variable can exist in multiple contexts (e.g., loop iterations).
    Cell manages all values for a single variable.

    Attributes:
        contexts: Dict mapping context_id to value
        default_value: Fallback value when context doesn't exist
    """

    __slots__ = ("contexts", "default_value")

    def __init__(self, default_value: Any = None):
        """Initialize Cell with a default value.

        Args:
            default_value: Value returned when context doesn't exist
        """
        self.contexts: Dict[tuple, Any] = {}  # context_id -> value
        self.default_value = default_value

    def __setitem__(self, context_id: Optional[str], value: Any) -> None:
        """Set value for a specific context.

        Args:
            context_id: Context ID (None = default context "main")
            value: Value to store
        """
        if context_id is None:
            context_id = DEFAULT_CONTEXT
        self.contexts[context_id] = value

    def __getitem__(self, context_id: Optional[str] = None) -> Any:
        """Get value from a specific context.

        Args:
            context_id: Context ID (None = default context "main")

        Returns:
            Context value or default_value if context doesn't exist
        """
        if context_id is None:
            context_id = DEFAULT_CONTEXT
        return self.contexts.get(context_id, self.default_value)

    def pop_context(self, context_id: str) -> Any:
        """Remove a context and return its value.

        Args:
            context_id: Context ID to remove

        Returns:
            Value of removed context or default_value if it didn't exist
        """
        return self.contexts.pop(context_id, self.default_value)

    def __delitem__(self, context_id: str) -> None:
        """Remove context: del cell['context_id']"""
        self.pop_context(context_id)

    def items(self):
        """Iterate (context_id, value) pairs."""
        return self.contexts.items()

    def __contains__(self, context_id: str) -> bool:
        """Check if context exists: 'context_id' in cell"""
        return context_id in self.contexts

    def __repr__(self) -> str:
        return f"Cell(contexts={len(self.contexts)}, default={self.default_value})"
