"""Trace exactly what happens with a > b >> c >> d >> e"""


class FakeNode:
    def __init__(self, name):
        self.name = name

    def __gt__(self, other):
        print(f"{self.name}.__gt__({other.name})")
        return other

    def __rshift__(self, other):
        print(f"{self.name}.__rshift__({other.name})")
        return other


a = FakeNode("a")
b = FakeNode("b")
c = FakeNode("c")
d = FakeNode("d")
e = FakeNode("e")

print("=== a > b >> c >> d >> e ===")
print("Precedence: >> (11) is higher than > (6)")
print("So Python parses as: a > (b >> c >> d >> e)")
print()
print("Execution order:")
result = a > b >> c >> d >> e
print()
print(f"Final result: {result.name}")
