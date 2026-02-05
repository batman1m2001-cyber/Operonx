"""Tutorial 01: Hello World — Workflow đầu tiên với Hush.

Không cần API key. Chỉ dùng hush-core.

Học được:
- GraphNode: container chứa workflow
- CodeNode: chạy Python function
- PARENT: truy cập data từ parent state
- START >> node >> END: kết nối nodes
- Hush engine: chạy workflow

Chạy: cd hush-tutorial && uv run python examples/01_hello_world.py
"""

import asyncio
from hush.core import Hush, GraphNode, CodeNode, START, END, PARENT


async def main():
    # =========================================================================
    # Ví dụ 1: Hello World đơn giản nhất
    # =========================================================================
    print("=" * 50)
    print("Ví dụ 1: Hello World")
    print("=" * 50)

    with GraphNode(name="hello-world") as graph:
        greet = CodeNode(
            name="greet",
            code_fn=lambda name: {"greeting": f"Xin chào, {name}!"},
            inputs={"name": PARENT["name"]},   # Lấy 'name' từ input
            outputs={"greeting": PARENT},       # Ghi 'greeting' lên parent state
        )
        START >> greet >> END

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
        greet = CodeNode(
            name="greet",
            code_fn=lambda name: {"greeting": f"Hello, {name}!"},
            inputs={"name": PARENT["name"]},
            outputs={"greeting": PARENT},
        )

        # Node 2: Chuyển thành uppercase
        upper = CodeNode(
            name="upper",
            code_fn=lambda text: {"result": text.upper()},
            inputs={"text": PARENT["greeting"]},  # Dùng output từ node trước
            outputs={"result": PARENT},
        )

        START >> greet >> upper >> END

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
        step_a = CodeNode(
            name="step_a",
            code_fn=lambda: {"a_result": "Kết quả A"},
            outputs={"a_result": PARENT},
        )
        step_b = CodeNode(
            name="step_b",
            code_fn=lambda: {"b_result": "Kết quả B"},
            outputs={"b_result": PARENT},
        )
        merge = CodeNode(
            name="merge",
            code_fn=lambda a, b: {"combined": f"{a} + {b}"},
            inputs={"a": PARENT["a_result"], "b": PARENT["b_result"]},
            outputs={"combined": PARENT},
        )

        # step_a và step_b chạy song song, rồi merge
        START >> [step_a, step_b] >> merge >> END

    engine = Hush(graph)
    result = await engine.run(inputs={})

    print(f"A: {result['a_result']}")
    print(f"B: {result['b_result']}")
    print(f"Combined: {result['combined']}")


if __name__ == "__main__":
    asyncio.run(main())
