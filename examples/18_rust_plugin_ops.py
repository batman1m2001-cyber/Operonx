"""Tutorial 18: Rust Plugin Ops — Viết và sử dụng Rust ops trong workflow.

Không cần API key. Cần rush-core.

Học được:
- @op(rust="func_name"): khai báo Rust built-in op
- Built-in Rust ops: double, add, math_sum, string_template, ...
- Mix Python ops và Rust built-in ops trong cùng workflow

Yêu cầu:
- rush-core đã build (cd rush-core && cargo build --release)

Chạy: cd tutorial && uv run python examples/18_rust_plugin_ops.py
"""

import asyncio

from hush.core import END, PARENT, START, GraphOp, Hush
from hush.core.ops import Each, ForOp, op

# =============================================================================
# Rust built-in ops — Python body là fallback khi chạy mode="python"
# =============================================================================


@op(rust="double")
def double(x: int):
    return {"result": x * 2}


@op(rust="add")
def add(a: int, b: int):
    match (isinstance(a, int), isinstance(b, int)):
        case (True, True):
            return {"result": a + b}
        case _:
            return {"result": float(a) + float(b)}


@op(rust="math_sum")
def math_sum(values: list):
    return {"result": sum(values)}


@op(rust="math_mean")
def math_mean(values: list):
    return {"result": sum(values) / len(values) if values else 0}


@op(rust="string_template")
def render_template(template: str, vars: dict):
    result = template
    for k, v in vars.items():
        result = result.replace(f"{{{k}}}", str(v))
    return {"result": result}


@op(rust="string_concat")
def concat_parts(parts: list):
    return {"result": "".join(parts)}


@op(rust="hash_chain")
def hash_chain(data: str, iterations: int):
    import hashlib

    current = data
    for _ in range(iterations):
        current = hashlib.md5(current.encode()).hexdigest()
    return {"hash": current}


# =============================================================================
# Examples
# =============================================================================


async def example_1_basic_plugin():
    """Ví dụ 1: Dùng built-in Rust ops."""
    print("=" * 60)
    print("Ví dụ 1: Built-in Rust Plugin Ops")
    print("=" * 60)

    with GraphOp(name="plugin-basic") as graph:
        d = double(x=PARENT["x"])
        a = add(a=d["result"], b=PARENT["y"])
        START >> d >> a >> END

    # Rust mode — dùng compiled Rust plugin
    engine = Hush(graph, mode="rust")
    result = await engine.run(inputs={"x": 7, "y": 3})
    print(f"  Rust mode:   double(7) + 3 = {result['result']}")
    assert result["result"] == 17

    # Python mode — dùng Python fallback body
    engine = Hush(graph)
    result = await engine.run(inputs={"x": 7, "y": 3})
    print(f"  Python mode: double(7) + 3 = {result['result']}")
    assert result["result"] == 17

    print("  Kết quả giống nhau!")


async def example_2_string_ops():
    """Ví dụ 2: String ops — template rendering."""
    print()
    print("=" * 60)
    print("Ví dụ 2: String Plugin Ops")
    print("=" * 60)

    with GraphOp(name="string-ops") as graph:
        rendered = render_template(
            template=PARENT["template"],
            vars=PARENT["vars"],
        )
        START >> rendered >> END

    engine = Hush(graph, mode="rust")
    result = await engine.run(
        inputs={
            "template": "Xin chào {name}, bạn có {count} tin nhắn mới!",
            "vars": {"name": "Hush", "count": "5"},
        }
    )

    print("  Template: 'Xin chào {name}, bạn có {count} tin nhắn mới!'")
    print(f"  Result:   '{result['result']}'")
    assert "Hush" in result["result"]
    assert "5" in result["result"]


async def example_3_math_ops():
    """Ví dụ 3: Math ops — sum và mean."""
    print()
    print("=" * 60)
    print("Ví dụ 3: Math Plugin Ops")
    print("=" * 60)

    with GraphOp(name="math-ops") as graph:
        # Dùng output mapping để tránh trùng key "result"
        s = math_sum(values=PARENT["numbers"], outputs={"result": PARENT["total"]})
        m = math_mean(values=PARENT["numbers"], outputs={"result": PARENT["average"]})
        START >> [s, m] >> END

    engine = Hush(graph, mode="rust")
    result = await engine.run(inputs={"numbers": [10, 20, 30, 40, 50]})

    print("  Input:   [10, 20, 30, 40, 50]")
    print(f"  Sum:     {result['total']}")
    print(f"  Average: {result['average']}")


async def example_4_mixed_ops():
    """Ví dụ 4: Mix Python ops và Rust plugin ops."""
    print()
    print("=" * 60)
    print("Ví dụ 4: Mix Python + Rust Plugin Ops")
    print("=" * 60)

    @op
    def build_report(total, label):
        """Python-only op: tạo report từ kết quả Rust ops."""
        return {"report": f"{label}: tổng = {total}"}

    with GraphOp(name="mixed-pipeline") as graph:
        # Rust plugin op: tính tổng
        s = math_sum(values=PARENT["numbers"])

        # Rust plugin op: concat labels
        c = concat_parts(parts=PARENT["labels"])

        # Python op: tổng hợp kết quả từ Rust ops
        r = build_report(total=s["result"], label=c["result"])

        START >> [s, c]
        [s, c] >> r >> END

    engine = Hush(graph, mode="rust")
    result = await engine.run(
        inputs={
            "numbers": [1, 2, 3, 4, 5],
            "labels": ["Kết", " quả", " tính", " toán"],
        }
    )

    print("  math_sum([1,2,3,4,5]) = 15")
    print("  concat(['Kết',' quả',' tính',' toán']) = 'Kết quả tính toán'")
    print(f"  Report: {result['report']}")


async def example_5_loop_with_plugins():
    """Ví dụ 5: ForOp loop với Rust plugin ops."""
    print()
    print("=" * 60)
    print("Ví dụ 5: ForOp Loop + Rust Plugin Ops")
    print("=" * 60)

    with GraphOp(name="loop-plugins") as graph:
        with ForOp.of(x=Each(PARENT["numbers"])) as loop:
            step = double(
                name="double",
                inputs={"x": PARENT["x"]},
                outputs={"*": PARENT},
            )
            START >> step >> END

        loop["result"] >> PARENT["doubled"]
        START >> loop >> END

    engine = Hush(graph, mode="rust")
    result = await engine.run(inputs={"numbers": [5, 10, 15, 20]})

    print("  Input:   [5, 10, 15, 20]")
    print(f"  Doubled: {result['doubled']}")
    assert result["doubled"] == [10, 20, 30, 40]


async def example_6_cpu_heavy():
    """Ví dụ 6: CPU-heavy op — hash chain benchmark."""
    print()
    print("=" * 60)
    print("Ví dụ 6: CPU-heavy Rust Plugin Op (hash_chain)")
    print("=" * 60)

    with GraphOp(name="hash-bench") as graph:
        h = hash_chain(data=PARENT["data"], iterations=PARENT["iterations"])
        START >> h >> END

    import time

    # Rust mode
    engine_rs = Hush(graph, mode="rust")
    t0 = time.perf_counter()
    result_rs = await engine_rs.run(inputs={"data": "hello", "iterations": 10000})
    rs_time = time.perf_counter() - t0

    # Python mode (fallback)
    engine_py = Hush(graph)
    t0 = time.perf_counter()
    result_py = await engine_py.run(inputs={"data": "hello", "iterations": 10000})
    py_time = time.perf_counter() - t0

    speedup = py_time / rs_time if rs_time > 0 else float("inf")

    print("  hash_chain('hello', 10000 iterations):")
    print(f"  Rust mode:   {rs_time * 1000:.1f}ms → {result_rs['hash'][:16]}...")
    print(f"  Python mode: {py_time * 1000:.1f}ms → {result_py['hash'][:16]}...")
    print(f"  Speedup:     {speedup:.1f}x")
    # Note: hash algorithms differ (Rust uses DefaultHasher, Python uses md5)
    # so the hash values will be different, but both are deterministic


async def main():
    await example_1_basic_plugin()
    await example_2_string_ops()
    await example_3_math_ops()
    await example_4_mixed_ops()
    await example_5_loop_with_plugins()
    await example_6_cpu_heavy()

    print()
    print("=" * 60)
    print("Tất cả ví dụ chạy thành công!")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
