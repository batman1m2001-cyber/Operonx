"""Test swapping >> and > roles to fix precedence issue."""


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

    # SWAPPED: > is now hard edge
    def __gt__(self, other):
        print(f"{self.name}.__gt__({other.name}) [HARD EDGE]")
        self.graph.add_edge(self.name, other.name, soft=False)
        return other

    # SWAPPED: >> is now soft edge
    def __rshift__(self, other):
        print(f"{self.name}.__rshift__({other.name}) [SOFT EDGE]")
        self.graph.add_edge(self.name, other.name, soft=True)
        return other


def test_soft_then_hard():
    """Test: a >> b > c (soft then hard)"""
    graph = FakeGraph()
    a = FakeNode("a", graph)
    b = FakeNode("b", graph)
    c = FakeNode("c", graph)

    print("\n=== Testing: a >> b > c ===")
    print("(>> precedence 11, > precedence 6)")
    print("Expected parse: (a >> b) > c")
    print("Expected edges: a->b soft, b->c hard")
    print()

    result = a >> b > c

    print()
    print(f"Edges: {graph.edges}")

    assert ("a", "b", "soft") in graph.edges, "Missing a->b soft"
    assert ("b", "c", "hard") in graph.edges, "Missing b->c hard"
    print("SUCCESS!")


def test_hard_then_soft():
    """Test: a > b >> c (hard then soft)"""
    graph = FakeGraph()
    a = FakeNode("a", graph)
    b = FakeNode("b", graph)
    c = FakeNode("c", graph)

    print("\n=== Testing: a > b >> c ===")
    print("(>> precedence 11, > precedence 6)")
    print("Expected parse: a > (b >> c)")
    print("Expected edges: b->c soft, a->c hard")
    print()

    result = a > b >> c

    print()
    print(f"Edges: {graph.edges}")

    # This will give us b->c soft, a->c hard (not a->b)
    assert ("b", "c", "soft") in graph.edges, "Missing b->c soft"
    assert ("a", "c", "hard") in graph.edges, "Missing a->c hard"
    print("This is the expected (but perhaps unwanted) behavior")


def test_branch_pattern():
    """Test typical branch pattern: START > branch >> [cases] > merge > END

    With swapped operators:
    - > is hard edge (normal flow)
    - >> is soft edge (branch output)

    Pattern: START > branch >> case1 > merge > END
                            >> case2 > merge
    """
    graph = FakeGraph()
    start = FakeNode("start", graph)
    branch = FakeNode("branch", graph)
    case1 = FakeNode("case1", graph)
    case2 = FakeNode("case2", graph)
    merge = FakeNode("merge", graph)
    end = FakeNode("end", graph)

    print("\n=== Testing branch pattern ===")
    print("start > branch (hard)")
    print("branch >> case1 (soft)")
    print("branch >> case2 (soft)")
    print("case1 > merge (hard)")
    print("case2 > merge (hard)")
    print("merge > end (hard)")
    print()

    start > branch
    branch >> case1
    branch >> case2
    case1 > merge
    case2 > merge
    merge > end

    print()
    print(f"Edges: {graph.edges}")

    assert ("start", "branch", "hard") in graph.edges
    assert ("branch", "case1", "soft") in graph.edges
    assert ("branch", "case2", "soft") in graph.edges
    assert ("case1", "merge", "hard") in graph.edges
    assert ("case2", "merge", "hard") in graph.edges
    assert ("merge", "end", "hard") in graph.edges
    print("SUCCESS!")


def test_all_hard():
    """Test: a > b > c > d (all hard edges)"""
    graph = FakeGraph()
    a = FakeNode("a", graph)
    b = FakeNode("b", graph)
    c = FakeNode("c", graph)
    d = FakeNode("d", graph)

    print("\n=== Testing: a > b > c > d (all hard) ===")
    print()

    result = a > b > c > d

    print()
    print(f"Edges: {graph.edges}")

    assert ("a", "b", "hard") in graph.edges
    assert ("b", "c", "hard") in graph.edges
    assert ("c", "d", "hard") in graph.edges
    print("SUCCESS!")


def test_all_soft():
    """Test: a >> b >> c >> d (all soft edges)"""
    graph = FakeGraph()
    a = FakeNode("a", graph)
    b = FakeNode("b", graph)
    c = FakeNode("c", graph)
    d = FakeNode("d", graph)

    print("\n=== Testing: a >> b >> c >> d (all soft) ===")
    print()

    result = a >> b >> c >> d

    print()
    print(f"Edges: {graph.edges}")

    assert ("a", "b", "soft") in graph.edges
    assert ("b", "c", "soft") in graph.edges
    assert ("c", "d", "soft") in graph.edges
    print("SUCCESS!")


def test_inline_branch():
    """Test: branch >> case1 > merge (inline soft then hard)"""
    graph = FakeGraph()
    branch = FakeNode("branch", graph)
    case1 = FakeNode("case1", graph)
    merge = FakeNode("merge", graph)

    print("\n=== Testing: branch >> case1 > merge ===")
    print("Expected: branch->case1 soft, case1->merge hard")
    print()

    branch >> case1 > merge

    print()
    print(f"Edges: {graph.edges}")

    assert ("branch", "case1", "soft") in graph.edges
    assert ("case1", "merge", "hard") in graph.edges
    print("SUCCESS!")


if __name__ == "__main__":
    test_soft_then_hard()
    test_hard_then_soft()
    test_branch_pattern()
    test_inline_branch()
    test_all_hard()
    test_all_soft()
