"""Test detecting mixed operators and raising helpful errors."""

import threading

# Thread-local storage to track last edge operation
_edge_context = threading.local()


def _get_last_edge():
    return getattr(_edge_context, 'last_edge', None)


def _set_last_edge(source, target, op_type):
    _edge_context.last_edge = (source, target, op_type)


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
        print(f"  Edge: {src} -> {dst} ({'soft' if soft else 'hard'})")


class FakeNode:
    def __init__(self, name, graph):
        self.name = name
        self.graph = graph

    def __gt__(self, other):
        """Soft edge: a > b"""
        last = _get_last_edge()

        # Check if 'other' was just the TARGET of a >> operation
        # That means: someone did `x >> other` and now we're doing `self > other`
        # This happens when: a > (b >> c) - b>>c runs first, returns c, then a > c
        if last and last[1] == other.name and last[2] == "hard":
            raise MixedOperatorError(
                f"Cannot mix >> and > in a single chain!\n"
                f"  Detected: '{self.name} > {other.name}' after '{last[0]} >> {last[1]}'\n"
                f"  Python parsed 'a > b >> c' as 'a > (b >> c)', not '(a > b) >> c'\n"
                f"  Solution: Break into separate lines:\n"
                f"    {self.name} > {last[0]}\n"
                f"    {last[0]} >> {other.name}"
            )

        self.graph.add_edge(self.name, other.name, soft=True)
        _set_last_edge(self.name, other.name, "soft")
        return other

    def __rshift__(self, other):
        """Hard edge: a >> b"""
        last = _get_last_edge()

        # Check if 'other' was just the TARGET of a > operation
        # This happens when: a >> (b > c) - b>c runs first, returns c, then a >> c
        if last and last[1] == other.name and last[2] == "soft":
            raise MixedOperatorError(
                f"Cannot mix >> and > in a single chain!\n"
                f"  Detected: '{self.name} >> {other.name}' after '{last[0]} > {last[1]}'\n"
                f"  Python parsed 'a >> b > c' as 'a >> (b > c)', not '(a >> b) > c'\n"
                f"  Solution: Break into separate lines:\n"
                f"    {self.name} >> {last[0]}\n"
                f"    {last[0]} > {other.name}"
            )

        self.graph.add_edge(self.name, other.name, soft=False)
        _set_last_edge(self.name, other.name, "hard")
        return other


def test_mixing_detected():
    """Test that mixing >> and > raises a helpful error."""
    graph = FakeGraph()
    a = FakeNode("a", graph)
    b = FakeNode("b", graph)
    c = FakeNode("c", graph)

    _clear_last_edge()

    print("\n=== Testing: a > b >> c (should raise error) ===\n")

    try:
        result = a > b >> c
        print("ERROR: Should have raised MixedOperatorError!")
    except MixedOperatorError as e:
        print(f"Caught expected error:\n{e}")
        print("\nSUCCESS: Error was detected and helpful message shown!")


def test_same_operator_ok():
    """Test that using same operator in chain is OK."""
    graph = FakeGraph()
    a = FakeNode("a", graph)
    b = FakeNode("b", graph)
    c = FakeNode("c", graph)

    _clear_last_edge()

    print("\n=== Testing: a >> b >> c (should work) ===\n")
    result = a >> b >> c
    print(f"Edges: {graph.edges}")
    assert ("a", "b", "hard") in graph.edges
    assert ("b", "c", "hard") in graph.edges
    print("SUCCESS!")


def test_separate_lines_ok():
    """Test that separate lines work fine."""
    graph = FakeGraph()
    a = FakeNode("a", graph)
    b = FakeNode("b", graph)
    c = FakeNode("c", graph)

    _clear_last_edge()

    print("\n=== Testing separate lines (should work) ===\n")
    a > b
    _clear_last_edge()  # Clear between statements
    b >> c
    print(f"Edges: {graph.edges}")
    assert ("a", "b", "soft") in graph.edges
    assert ("b", "c", "hard") in graph.edges
    print("SUCCESS!")


if __name__ == "__main__":
    test_mixing_detected()
    test_same_operator_ok()
    test_separate_lines_ok()
