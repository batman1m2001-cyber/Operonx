"""Tutorial 17: Rust Mode — Tăng tốc workflow với Rust execution backend.

Không cần API key. Chỉ dùng hush-core + rush-core.

Học được:
- mode="rust": chuyển sang Rust execution backend
- So sánh kết quả Python mode vs Rust mode
- Rust mode hoạt động với tất cả op types (ForOp, MapOp, fan-out, nested graphs)
- Benchmark performance giữa hai modes

Yêu cầu: rush-core đã build (cd rush-core && uv run maturin develop --release)

Chạy: cd tutorial && uv run python examples/17_rust_mode.py
"""

import asyncio
import time

from hush.core import END, PARENT, START, GraphOp, Hush
from hush.core.ops import Each, ForOp, MapOp, op

# =============================================================================
# Định nghĩa ops
# =============================================================================


@op
def double(x: int):
    """Nhân đôi số."""
    return {"result": x * 2}


@op
def add(a: int, b: int):
    """Cộng hai số."""
    return {"result": a + b}


@op
def square(x: int):
    """Bình phương."""
    return {"squared": x * x}


@op
def sum_values(values: list):
    """Tính tổng."""
    return {"total": sum(values)}


# =============================================================================
# Examples
# =============================================================================


async def example_1_basic():
    """Ví dụ cơ bản: cùng workflow, hai modes."""
    print("=" * 60)
    print("Ví dụ 1: Python mode vs Rust mode — Linear chain")
    print("=" * 60)

    with GraphOp(name="basic-chain") as graph:
        d = double(x=PARENT["x"])
        a = add(a=d["result"], b=PARENT["y"])
        START >> d >> a >> END

    # Python mode
    engine_py = Hush(graph)
    result_py = await engine_py.run(inputs={"x": 5, "y": 3})
    print(f"  Python mode: double(5) + 3 = {result_py['result']}")

    # Rust mode
    engine_rs = Hush(graph, mode="rust")
    result_rs = await engine_rs.run(inputs={"x": 5, "y": 3})
    print(f"  Rust mode:   double(5) + 3 = {result_rs['result']}")

    assert result_py["result"] == result_rs["result"] == 13
    print("  Kết quả giống nhau!")


async def example_2_fan_out():
    """Fan-out/fan-in: nhiều ops chạy song song."""
    print()
    print("=" * 60)
    print("Ví dụ 2: Fan-out / Fan-in")
    print("=" * 60)

    @op
    def task_a(x: int):
        return {"a": x * 10}

    @op
    def task_b(x: int):
        return {"b": x * 20}

    @op
    def task_c(x: int):
        return {"c": x * 30}

    @op
    def merge(a: int, b: int, c: int):
        return {"total": a + b + c}

    with GraphOp(name="fan-out") as graph:
        a = task_a(x=PARENT["x"])
        b = task_b(x=PARENT["x"])
        c = task_c(x=PARENT["x"])
        m = merge(a=a["a"], b=b["b"], c=c["c"])

        START >> [a, b, c] >> m >> END

    engine = Hush(graph, mode="rust")
    result = await engine.run(inputs={"x": 5})

    print("  Input: x=5")
    print(f"  a=50, b=100, c=150 → total={result['total']}")
    assert result["total"] == 300


async def example_3_for_loop():
    """ForOp loop trong Rust mode."""
    print()
    print("=" * 60)
    print("Ví dụ 3: ForOp loop trong Rust mode")
    print("=" * 60)

    with GraphOp(name="for-loop") as graph:
        with ForOp.of(x=Each(PARENT["numbers"])) as loop:
            step = square(
                name="square",
                inputs={"x": PARENT["x"]},
                outputs={"*": PARENT},
            )
            START >> step >> END

        loop["squared"] >> PARENT["results"]
        START >> loop >> END

    engine = Hush(graph, mode="rust")
    result = await engine.run(inputs={"numbers": [1, 2, 3, 4, 5]})

    print("  Input:    [1, 2, 3, 4, 5]")
    print(f"  Squared:  {result['results']}")
    assert result["results"] == [1, 4, 9, 16, 25]


async def example_4_map_op():
    """MapOp parallel trong Rust mode."""
    print()
    print("=" * 60)
    print("Ví dụ 4: MapOp parallel trong Rust mode")
    print("=" * 60)

    with GraphOp(name="map-parallel") as graph:
        with MapOp.of(x=Each(PARENT["numbers"]), max_concurrency=3) as map_op:
            step = double(
                name="double",
                inputs={"x": PARENT["x"]},
                outputs={"*": PARENT},
            )
            START >> step >> END

        map_op["result"] >> PARENT["doubled"]
        START >> map_op >> END

    engine = Hush(graph, mode="rust")
    result = await engine.run(inputs={"numbers": [10, 20, 30, 40, 50]})

    print("  Input:   [10, 20, 30, 40, 50]")
    print(f"  Doubled: {result['doubled']}")
    assert result["doubled"] == [20, 40, 60, 80, 100]


async def example_5_benchmark():
    """So sánh performance Python vs Rust."""
    print()
    print("=" * 60)
    print("Ví dụ 5: Benchmark — Linear chain (100 ops)")
    print("=" * 60)

    # Tạo workflow với nhiều ops
    with GraphOp(name="bench-chain") as graph:
        prev = double(name="step_0", inputs={"x": PARENT["x"]})
        START >> prev

        for i in range(1, 100):
            curr = double(name=f"step_{i}", inputs={"x": prev["result"]})
            prev >> curr
            prev = curr

        prev >> END

    inputs = {"x": 1}

    # Warm up
    engine_py = Hush(graph)
    await engine_py.run(inputs=inputs)

    engine_rs = Hush(graph, mode="rust")
    engine_rs.run(inputs=inputs) if False else None  # noqa

    # Benchmark Python
    runs = 5
    t0 = time.perf_counter()
    for _ in range(runs):
        await engine_py.run(inputs=inputs)
    py_time = (time.perf_counter() - t0) / runs

    # Benchmark Rust
    t0 = time.perf_counter()
    for _ in range(runs):
        await engine_rs.run(inputs=inputs)
    rs_time = (time.perf_counter() - t0) / runs

    speedup = py_time / rs_time if rs_time > 0 else float("inf")

    print(f"  Python mode: {py_time * 1000:.1f}ms / run")
    print(f"  Rust mode:   {rs_time * 1000:.1f}ms / run")
    print(f"  Speedup:     {speedup:.1f}x")


async def main():
    await example_1_basic()
    await example_2_fan_out()
    await example_3_for_loop()
    await example_4_map_op()
    await example_5_benchmark()

    print()
    print("=" * 60)
    print("Tất cả ví dụ chạy thành công!")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
