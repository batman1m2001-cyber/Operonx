# Rust Mode và Plugin Ops

Tăng tốc workflow 2-6x với Rust execution backend và tạo Rust plugin ops riêng.

> **Ví dụ chạy được**: `examples/17_rust_mode.py`, `examples/18_rust_plugin_ops.py`

> **Shorthand syntax:** Các ví dụ trong chương này sử dụng shorthand syntax cho gọn.
> Xem [Shorthand Reference](12-shorthand-syntax.md) để biết đầy đủ.
>
> | Syntax | Class | Ví dụ |
> |--------|-------|-------|
> | `@op` | `FuncOp` | `@op` decorator trên function |
> | `@op(rust=...)` | `FuncOp` | Rust plugin op với Python fallback |
> | `MapOp.of()` | `MapOp` | `MapOp.of(x=Each([1,2,3]))` |
> | `ForOp.of()` | `ForOp` | `ForOp.of(x=Each([1,2,3]))` |

## Giới thiệu

**Rust mode** là execution backend thay thế cho Python mode mặc định. Thay vì dùng Python asyncio để schedule ops, Rust mode dùng **rush-core** (viết bằng Rust + PyO3) với:

- **DashMap** cho concurrent state — thread-safe, lock-free reads
- **rayon** cho parallel execution — fan-out/fan-in chạy thật song song
- **tokio** cho async I/O — LLM calls, embeddings chạy concurrent

Kết quả: **2-6x nhanh hơn** cho hầu hết workflow patterns.

## Cài đặt rush-core

```bash
cd rush-core && cargo build --release
```

> **Lưu ý:** Cần Rust toolchain (`rustup`).

## Sử dụng Rust mode

Chỉ cần thêm `mode="rust"` khi khởi tạo `Hush` engine:

```python
from hush.core import Hush, GraphOp, op, START, END, PARENT

@op
def double(x: int):
    return {"result": x * 2}

async def main():
    with GraphOp(name="demo") as graph:
        step = double(x=PARENT["x"])
        START >> step >> END

    # Python mode (mặc định)
    engine_py = Hush(graph)
    result = await engine_py.run(inputs={"x": 5})

    # Rust mode — nhanh hơn 2-6x
    engine_rs = Hush(graph, mode="rust")
    result = await engine_rs.run(inputs={"x": 5})
    # Kết quả giống nhau: {"result": 10}
```

> **Fallback tự động:** Nếu rush-core chưa cài, engine tự chuyển về Python mode và log warning.

### Benchmark so sánh

| Pattern | Tốc độ Rust vs Python |
|---------|----------------------|
| Linear chain (50-500 ops) | 2.3x – 2.7x |
| Nested @graph (2-20 stages) | 3.4x – 3.9x |
| Parallel fan-out (5-50 branches) | 2.9x – 3.2x |
| ForOp loop (10-100 items) | 3.0x – 3.3x |
| MapOp parallel (10-50 items) | 2.5x – 3.0x |
| CPU contention | 2.4x – 6.1x |

### Parallel execution trong Rust mode

Rust mode sử dụng **batch-aware scheduler**:
- Khi nhiều ops ready cùng lúc → chạy song song qua rayon/tokio
- Python ops: release GIL, chạy song song nếu I/O-bound
- Rust plugin ops: chạy hoàn toàn ngoài GIL
- State dùng DashMap (concurrent HashMap) — thread-safe

## Rust Plugin Ops

Ngoài Rust mode (tăng tốc scheduling), bạn có thể viết **Rust plugin ops** — các op được compile thành shared library (.so/.dylib) và load tại runtime.

### Tại sao dùng Rust plugin ops?

- **CPU-bound tasks** — hash chains, data processing, tính toán nặng
- **Không cần GIL** — chạy song song thực sự trong Rust mode
- **Python fallback** — cùng function body chạy được ở cả Python mode

### Cách sử dụng

Dùng `@op(rust="<path>::<func>")` decorator:

```python
@op(rust="./examples/rush-ops-builtin::double")
def double(x: int):
    return {"result": x * 2}  # Python fallback
```

- `./examples/rush-ops-builtin` — đường dẫn tới crate (tương đối từ working directory)
- `double` — tên function trong Rust crate

Engine tự động build crate (`cargo build --release`) lần đầu, cache kết quả cho lần sau.

### Built-in Rust ops

Hush đi kèm **rush-ops-builtin** với 13 ops sẵn có:

| Category | Op | Input → Output |
|----------|----|---------------|
| **Core** | `double` | `x → result = x * 2` |
| | `add` | `a, b → result = a + b` |
| | `hash_chain` | `data, iterations → hash` (CPU-heavy) |
| **String** | `string_concat` | `parts: list[str] → result: str` |
| | `string_split` | `text, delimiter → parts: list[str]` |
| | `string_template` | `template, vars: dict → result` |
| **JSON** | `json_parse` | `text → data` (parse JSON string) |
| | `json_extract` | `data, path → value` (dot-separated path) |
| | `json_merge` | `a: dict, b: dict → result` (shallow merge) |
| **Math** | `math_sum` | `values: list → result` |
| | `math_mean` | `values: list → result` |
| | `math_max` | `values: list → result` |
| | `math_min` | `values: list → result` |

Ví dụ:

```python
@op(rust="./examples/rush-ops-builtin::math_sum")
def sum_values(values: list):
    return {"result": sum(values)}

@op(rust="./examples/rush-ops-builtin::string_template")
def render(template: str, vars: dict):
    result = template
    for k, v in vars.items():
        result = result.replace(f"{{{k}}}", str(v))
    return {"result": result}
```

## Tạo Rust Plugin

### Bước 1: Tạo crate

```bash
mkdir -p my-ops/src
```

### Bước 2: Cargo.toml

```toml
[package]
name = "my-ops"
version = "0.1.0"
edition = "2021"

[lib]
crate-type = ["cdylib"]   # Bắt buộc: compile thành shared library

[dependencies]
rush-ops-sdk = { path = "../rush-core/sdk" }
serde_json = "1"
```

> **Quan trọng:** `crate-type = ["cdylib"]` là bắt buộc. Nếu thiếu, library sẽ không load được.

### Bước 3: Viết ops (src/lib.rs)

```rust
use rush_ops_sdk::{export_ops, serde_json};
use serde_json::Value;

/// Nhân hai số
fn multiply(inputs: &Value) -> Value {
    let a = inputs["a"].as_i64().unwrap_or(0);
    let b = inputs["b"].as_i64().unwrap_or(0);
    serde_json::json!({"result": a * b})
}

/// Chuyển text thành uppercase
fn uppercase(inputs: &Value) -> Value {
    let text = inputs["text"].as_str().unwrap_or("");
    serde_json::json!({"result": text.to_uppercase()})
}

// Export qua C ABI — tự động tạo rush_op_multiply, rush_op_uppercase
export_ops!(multiply, uppercase);
```

**Quy tắc:**
- Signature: `fn(&serde_json::Value) -> serde_json::Value`
- Input: JSON object, đọc fields bằng `inputs["key"]`
- Output: JSON object, thường là `{"result": value}` hoặc `{"error": msg}`
- `export_ops!` nhận danh sách function names, phân cách bằng dấu phẩy

### Bước 4: Sử dụng trong Python

```python
from hush.core import Hush, GraphOp, op, START, END, PARENT

@op(rust="./my-ops::multiply")
def multiply(a: int, b: int):
    return {"result": a * b}  # Python fallback

@op(rust="./my-ops::uppercase")
def uppercase(text: str):
    return {"result": text.upper()}  # Python fallback

async def main():
    with GraphOp(name="custom-ops") as graph:
        m = multiply(a=PARENT["x"], b=PARENT["y"])
        u = uppercase(text=PARENT["text"])
        START >> [m, u] >> END

    # Rust mode — dùng compiled plugin, tự build lần đầu
    engine = Hush(graph, mode="rust")
    result = await engine.run(inputs={"x": 3, "y": 4, "text": "hello"})
    print(result["result"])  # Từ uppercase: "HELLO"

    # Python mode — dùng Python fallback body
    engine = Hush(graph)
    result = await engine.run(inputs={"x": 3, "y": 4, "text": "hello"})
    # Kết quả giống nhau
```

> **Auto-build:** Lần đầu chạy với `mode="rust"`, engine tự phát hiện crate directory, chạy `cargo build --release`, và cache đường dẫn library. Lần sau sẽ load trực tiếp.

## Kết hợp Python ops và Rust plugin ops

Bạn có thể mix Python ops và Rust ops trong cùng workflow:

```python
@op
def fetch_data():
    """Python op — I/O bound, không cần Rust."""
    return {"data": [1, 2, 3, 4, 5]}

@op(rust="./examples/rush-ops-builtin::math_sum")
def sum_values(values: list):
    """Rust plugin — CPU bound, hưởng lợi từ Rust."""
    return {"result": sum(values)}

with GraphOp(name="mixed") as graph:
    f = fetch_data()
    s = sum_values(values=f["data"])
    START >> f >> s >> END

engine = Hush(graph, mode="rust")
result = await engine.run(inputs={})
# fetch_data chạy Python (qua GIL callback)
# sum_values chạy Rust plugin (ngoài GIL)
```

## Bound hints (`bound="io"` / `bound="cpu"`)

Scheduler hint cho Rust mode biết cách schedule op:

```python
@op(bound="io")
async def call_api(url: str):
    """I/O-bound: schedule qua tokio async runtime."""
    import aiohttp
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            return {"data": await resp.json()}

@op(bound="cpu")
def heavy_compute(data: list):
    """CPU-bound: schedule qua rayon thread pool."""
    return {"result": sum(x * x for x in data)}
```

| Bound | Scheduler | Khi nào dùng |
|-------|-----------|-------------|
| `"io"` | tokio async | HTTP calls, LLM API, database |
| `"cpu"` | rayon threads | Computation, data processing |
| `None` (mặc định) | Auto-detect | async → `"io"`, sync → `"cpu"` |

## Khi nào nên dùng Rust mode?

| Use case | Khuyến nghị |
|----------|-------------|
| Workflow có nhiều ops nhẹ (data transformation) | Rust mode |
| CPU-bound workloads (hash chains, math) | Rust mode + Rust plugin ops |
| Production deployment cần throughput cao | Rust mode |
| I/O-bound workflow (chủ yếu LLM calls) | Python mode hoặc Rust mode đều được |
| Rapid prototyping / debugging | Python mode |

## Hạn chế hiện tại

- **Async ops** chạy sync-style trong Rust scheduler (tokio handles concurrency, nhưng khác với Python asyncio)
- **Streaming** hỗ trợ cho LLM ops, nhưng chưa hỗ trợ cho custom plugin ops
- Cần **Rust toolchain** để build plugins (auto-build cần `cargo` trong PATH)

## Tiếp theo

- [Parallel Execution](08-parallel-execution.md) — Fan-out/fan-in, MapOp
- [Tracing & Observability](09-tracing-observability.md) — Debug workflows (hỗ trợ cả Rust mode)
