"""Tutorial 01: Hello World — Workflow đầu tiên với Hush.

Không cần API key. Chỉ dùng hush-core.

Học được:
- GraphOp: container chứa workflow
- @op: decorator tạo op từ Python function
- @op(rust="..."): tag Rust implementation, Python body là fallback
- PARENT: truy cập data từ parent state
- START >> step >> END: kết nối ops
- Hush engine: chạy workflow bằng Python
- engine.serve(): serve workflow qua HTTP (Python hoặc Rust backend)

Chạy: uv run python examples/01_hello_world.py
"""

import asyncio
import sys

from hush.core import END, PARENT, START, GraphOp, Hush
from hush.core.ops.transform.func_op import op

# =============================================================================
# Định nghĩa code ops với @op decorator
# Mỗi op có rust= tag → khi serve(backend="rust"), Rust implementation được dùng
# =============================================================================


@op(rust="./rust_ops::text::greet_vi")
def greet(name: str):
    """Tạo greeting từ tên."""
    return {"greeting": f"Xin chào, {name}!"}


@op(rust="./rust_ops::text::greet")
def greet_en(name: str):
    """Tạo greeting tiếng Anh."""
    return {"greeting": f"Hello, {name}!"}


@op(rust="./rust_ops::text::to_upper")
def upper(text: str):
    """Chuyển thành uppercase."""
    return {"result": text.upper()}


@op(rust="./rust_ops::pipeline::step_a")
def step_a():
    return {"a_result": "Kết quả A"}


@op(rust="./rust_ops::pipeline::step_b")
def step_b():
    return {"b_result": "Kết quả B"}


@op(rust="./rust_ops::pipeline::merge_two")
def merge(a: str, b: str):
    return {"combined": f"{a} + {b}"}


async def main():
    # =========================================================================
    # Ví dụ 1: Hello World đơn giản nhất
    # =========================================================================
    print("=" * 50)
    print("Ví dụ 1: Hello World")
    print("=" * 50)

    with GraphOp(name="hello-world") as graph:
        g = greet(name=PARENT["name"])
        START >> g >> END

    # Chạy workflow bằng Python engine
    engine = Hush(graph)
    result = await engine.run(inputs={"name": "Hush"})
    print(f"Result: {result['greeting']}")

    # =========================================================================
    # Ví dụ 2: Hai ops nối tiếp
    # =========================================================================
    print()
    print("=" * 50)
    print("Ví dụ 2: Hai ops nối tiếp")
    print("=" * 50)

    with GraphOp(name="two-steps") as graph:
        # Op 1: Tạo greeting
        g = greet_en(name=PARENT["name"], outputs={"*": PARENT})

        # Op 2: Chuyển thành uppercase — đọc output từ op g
        u = upper(text=g["greeting"])

        START >> g >> u >> END

    engine = Hush(graph)
    result = await engine.run(inputs={"name": "Hush User"})

    print(f"Greeting: {result['greeting']}")
    print(f"Uppercase: {result['result']}")
    # Output: HELLO, HUSH USER!

    # =========================================================================
    # Ví dụ 3: Ops chạy song song
    # =========================================================================
    print()
    print("=" * 50)
    print("Ví dụ 3: Ops song song")
    print("=" * 50)

    with GraphOp(name="parallel") as graph:
        a = step_a()
        b = step_b()
        m = merge(a=a["a_result"], b=b["b_result"])

        # step_a và step_b chạy song song, rồi merge
        START >> [a, b] >> m >> END

    engine = Hush(graph)
    result = await engine.run(inputs={})

    print(f"Combined: {result['combined']}")

    # =========================================================================
    # Serve workflow qua HTTP
    # =========================================================================
    #
    # Python backend (FastAPI + uvicorn):
    #   engine.serve(port=8000)
    #
    # Rust backend (Axum — dùng rust= tag trong mỗi @op):
    #   engine.serve(port=8000, backend="rust")
    #
    # Cả hai đều expose cùng API:
    #   POST / {"name": "Hush"} → {"greeting": "Xin chào, Hush!"}


if __name__ == "__main__":
    asyncio.run(main())
