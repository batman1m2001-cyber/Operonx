"""Test different operators for soft edge that have higher precedence than >>."""


class FakeGraph:
    def __init__(self):
        self.edges = []

    def add_edge(self, src, dst, soft=False):
        self.edges.append((src, dst, "soft" if soft else "hard"))
        print(f"  Added edge: {src} -> {dst} ({'soft' if soft else 'hard'})")


class FakeNode:
    def __init__(self, name, graph):
        self.name = name
        self.graph = graph

    # Hard edge: >>
    def __rshift__(self, other):
        print(f"{self.name}.__rshift__({other.name})")
        self.graph.add_edge(self.name, other.name, soft=False)
        return other

    # Option 1: @ (matmul) - precedence 14 (higher than >> which is 11)
    def __matmul__(self, other):
        print(f"{self.name}.__matmul__({other.name})")
        self.graph.add_edge(self.name, other.name, soft=True)
        return other

    # Option 2: | (or) - precedence 8 (lower than >> which is 11) - WON'T WORK
    def __or__(self, other):
        print(f"{self.name}.__or__({other.name})")
        self.graph.add_edge(self.name, other.name, soft=True)
        return other

    # Option 3: ^ (xor) - precedence 9 (lower than >> which is 11) - WON'T WORK
    def __xor__(self, other):
        print(f"{self.name}.__xor__({other.name})")
        self.graph.add_edge(self.name, other.name, soft=True)
        return other

    # Option 4: * (mul) - precedence 13 (higher than >>)
    def __mul__(self, other):
        print(f"{self.name}.__mul__({other.name})")
        self.graph.add_edge(self.name, other.name, soft=True)
        return other


def test_matmul_operator():
    """Test @ operator for soft edge: a @ b >> c"""
    graph = FakeGraph()
    a = FakeNode("a", graph)
    b = FakeNode("b", graph)
    c = FakeNode("c", graph)

    print("\n=== Testing: a @ b >> c ===")
    print("(@ has precedence 14, >> has precedence 11)")
    print("Expected: (a @ b) >> c -> a->b soft, b->c hard")
    print()

    result = a @ b >> c

    print()
    print(f"Edges: {graph.edges}")

    assert ("a", "b", "soft") in graph.edges
    assert ("b", "c", "hard") in graph.edges


def test_or_operator():
    """Test | operator for soft edge: a | b >> c"""
    graph = FakeGraph()
    a = FakeNode("a", graph)
    b = FakeNode("b", graph)
    c = FakeNode("c", graph)

    print("\n=== Testing: a | b >> c ===")
    print("(| has precedence 8, >> has precedence 11)")
    print("Expected to FAIL: a | (b >> c) -> b->c hard, a->c soft")
    print()

    result = a | b >> c

    print()
    print(f"Edges: {graph.edges}")

    # This is expected to fail due to operator precedence
    # | has lower precedence than >>, so it parses as: a | (b >> c)
    assert ("b", "c", "hard") in graph.edges
    assert ("a", "c", "soft") in graph.edges


def test_mul_operator():
    """Test * operator for soft edge: a * b >> c"""
    graph = FakeGraph()
    a = FakeNode("a", graph)
    b = FakeNode("b", graph)
    c = FakeNode("c", graph)

    print("\n=== Testing: a * b >> c ===")
    print("(* has precedence 13, >> has precedence 11)")
    print("Expected: (a * b) >> c -> a->b soft, b->c hard")
    print()

    result = a * b >> c

    print()
    print(f"Edges: {graph.edges}")

    assert ("a", "b", "soft") in graph.edges
    assert ("b", "c", "hard") in graph.edges


if __name__ == "__main__":
    print("=" * 60)
    print("Python Operator Precedence (high to low):")
    print("  14: @")
    print("  13: *, /, //, %")
    print("  12: +, -")
    print("  11: <<, >>")
    print("  10: &")
    print("   9: ^")
    print("   8: |")
    print("   6: <, <=, >, >=, ==, !=")
    print("=" * 60)

    test_matmul_operator()
    test_or_operator()
    test_mul_operator()
