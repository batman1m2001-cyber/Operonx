"""Test detecting mixed operators and cleaning up the wrong edge."""

import threading

_edge_context = threading.local()


def _get_last_edge():
    return getattr(_edge_context, 'last_edge', None)


def _set_last_edge(source, target, op_type, graph):
    _edge_context.last_edge = (source, target, op_type, graph)


def _clear_last_edge():
    _edge_context.last_edge = None


class MixedOperatorError(Exception):
    """Raised when >> and > are mixed in a single chain."""
    pass


class FakeGraph:
    def __init__(self):
        self.edges = []

    def add_edge(self, src, dst, soft=False):
        self.edges.append((src, dst, "soft" if soft else "hard"))

    def remove_edge(self, src, dst):
        """Remove an edge (for cleanup on error)."""
        self.edges = [(s, d, t) for s, d, t in self.edges if not (s == src and d == dst)]


class FakeNode:
    def __init__(self, name, graph):
        self.name = name
        self.graph = graph

    def __gt__(self, other):
        """Soft edge: a > b"""
        last = _get_last_edge()

        # Detect: we're doing `self > other` but `other` was just target of >>
        # This means: `x >> other` happened, now `self > other`
        # The user wrote: `self > x >> other` which parsed as `self > (x >> other)`
        if last and last[1] == other.name and last[2] == "hard":
            # Remove the incorrectly added edge
            last[3].remove_edge(last[0], last[1])
            _clear_last_edge()

            raise MixedOperatorError(
                f"Cannot mix '>>' and '>' in a single expression!\n\n"
                f"  You wrote something like: {self.name} > {last[0]} >> {other.name}\n"
                f"  Python parsed this as:    {self.name} > ({last[0]} >> {other.name})\n"
                f"  Which creates wrong edges: {last[0]}->{other.name}, {self.name}->{other.name}\n\n"
                f"  Solution - use separate lines:\n"
                f"      {self.name} > {last[0]}\n"
                f"      {last[0]} >> {other.name}"
            )

        self.graph.add_edge(self.name, other.name, soft=True)
        _set_last_edge(self.name, other.name, "soft", self.graph)
        return other

    def __rshift__(self, other):
        """Hard edge: a >> b"""
        last = _get_last_edge()

        # Detect: we're doing `self >> other` but `other` was just target of >
        if last and last[1] == other.name and last[2] == "soft":
            last[3].remove_edge(last[0], last[1])
            _clear_last_edge()

            raise MixedOperatorError(
                f"Cannot mix '>>' and '>' in a single expression!\n\n"
                f"  You wrote something like: {self.name} >> {last[0]} > {other.name}\n"
                f"  Python parsed this as:    {self.name} >> ({last[0]} > {other.name})\n"
                f"  Which creates wrong edges: {last[0]}->{other.name}, {self.name}->{other.name}\n\n"
                f"  Solution - use separate lines:\n"
                f"      {self.name} >> {last[0]}\n"
                f"      {last[0]} > {other.name}"
            )

        self.graph.add_edge(self.name, other.name, soft=False)
        _set_last_edge(self.name, other.name, "hard", self.graph)
        return other


def test_detect_and_cleanup():
    """Test that mixing is detected and wrong edge is removed."""
    graph = FakeGraph()
    a = FakeNode("a", graph)
    b = FakeNode("b", graph)
    c = FakeNode("c", graph)

    _clear_last_edge()

    print("=== Testing: a > b >> c ===\n")
    print("Before:", graph.edges)

    try:
        a > b >> c
        print("ERROR: Should have raised!")
    except MixedOperatorError as e:
        print(f"Caught error:\n{e}\n")
        print("After cleanup:", graph.edges)
        assert len(graph.edges) == 0, "Wrong edge should have been removed"
        print("\nSUCCESS: Error detected and wrong edge cleaned up!")


def test_reverse_mixing():
    """Test: a >> b > c"""
    graph = FakeGraph()
    a = FakeNode("a", graph)
    b = FakeNode("b", graph)
    c = FakeNode("c", graph)

    _clear_last_edge()

    print("\n=== Testing: a >> b > c ===\n")

    try:
        a >> b > c
        print("ERROR: Should have raised!")
    except MixedOperatorError as e:
        print(f"Caught error:\n{e}\n")
        print("After cleanup:", graph.edges)
        assert len(graph.edges) == 0
        print("\nSUCCESS!")


def test_valid_chain():
    """Test that valid chains still work."""
    graph = FakeGraph()
    a = FakeNode("a", graph)
    b = FakeNode("b", graph)
    c = FakeNode("c", graph)

    _clear_last_edge()

    print("\n=== Testing: a >> b >> c (valid) ===\n")
    a >> b >> c
    print("Edges:", graph.edges)
    assert ("a", "b", "hard") in graph.edges
    assert ("b", "c", "hard") in graph.edges
    print("SUCCESS!")


if __name__ == "__main__":
    test_detect_and_cleanup()
    test_reverse_mixing()
    test_valid_chain()
