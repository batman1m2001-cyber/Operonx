"""Tutorial 01: Hello World — Workflow đầu tiên với Hush.

Không cần API key. Chỉ dùng hush-core.

Học được:
- GraphNode: container chứa workflow
- @code_node: decorator tạo node từ Python function
- PARENT: truy cập data từ parent state
- START >> node >> END: kết nối nodes
- Hush engine: chạy workflow

Chạy: cd hush-tutorial && uv run python examples/01_hello_world.py
"""

import asyncio

from hush.core import END, PARENT, START, GraphNode, Hush
from hush.core.nodes.transform.code_node import code_node

# =============================================================================
# Định nghĩa code nodes với @code_node decorator
# =============================================================================


@code_node
def greet(name: str):
    """Tạo greeting từ tên."""
    return {"greeting": f"Xin chào, {name}!"}


@code_node
def greet_en(name: str):
    """Tạo greeting tiếng Anh."""
    return {"greeting": f"Hello, {name}!"}


@code_node
def upper(text: str):
    """Chuyển thành uppercase."""
    return {"result": text.upper()}


@code_node
def step_a():
    return {"a_result": "Kết quả A"}


@code_node
def step_b():
    return {"b_result": "Kết quả B"}


@code_node
def merge(a: str, b: str):
    return {"combined": f"{a} + {b}"}


async def main():
    # =========================================================================
    # Ví dụ 1: Hello World đơn giản nhất
    # =========================================================================
    print("=" * 50)
    print("Ví dụ 1: Hello World")
    print("=" * 50)

    with GraphNode(name="hello-world") as graph:
        g = greet(name=PARENT["name"])
        START >> g >> END

    engine = Hush(graph)
    result = await engine.run(inputs={"name": "Hush"})

    print(f"Kết quả: {result['greeting']}")
    # Output: Xin chào, Hush!

    # =========================================================================
    # Ví dụ 2: Hai nodes nối tiếp
    # =========================================================================
    print()
    print("=" * 50)
    print("Ví dụ 2: Hai nodes nối tiếp")
    print("=" * 50)

    with GraphNode(name="two-steps") as graph:
        # Node 1: Tạo greeting
        g = greet_en(name=PARENT["name"], outputs={"*": PARENT})

        # Node 2: Chuyển thành uppercase — đọc output từ node g
        u = upper(text=g["greeting"])

        START >> g >> u >> END

    engine = Hush(graph)
    result = await engine.run(inputs={"name": "Hush User"})

    print(f"Greeting: {result['greeting']}")
    print(f"Uppercase: {result['result']}")
    # Output: HELLO, HUSH USER!

    # =========================================================================
    # Ví dụ 3: Nodes chạy song song
    # =========================================================================
    print()
    print("=" * 50)
    print("Ví dụ 3: Nodes song song")
    print("=" * 50)

    with GraphNode(name="parallel") as graph:
        a = step_a()
        b = step_b()
        m = merge(a=a["a_result"], b=b["b_result"])

        # step_a và step_b chạy song song, rồi merge
        START >> [a, b] >> m >> END

    engine = Hush(graph)
    result = await engine.run(inputs={})

    print(f"Combined: {result['combined']}")


if __name__ == "__main__":
    asyncio.run(main())
