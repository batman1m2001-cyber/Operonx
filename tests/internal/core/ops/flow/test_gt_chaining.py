"""Test how Python handles > chaining."""


class FakeNode:
    def __init__(self, name):
        self.name = name

    def __gt__(self, other):
        print(f"{self.name}.__gt__({other.name})")
        return other  # Return other to allow chaining


a = FakeNode("a")
b = FakeNode("b")
c = FakeNode("c")

print("=== Testing: a > b > c ===")
print("Python comparison chaining: (a > b) and (b > c)")
print()

result = a > b > c

print()
print(f"Result type: {type(result)}")
print(f"Result value: {result}")
