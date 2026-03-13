# Rust Mode và Plugin Ops

Tăng tốc workflow với Rust execution backend và tạo Rust plugin ops riêng.

> **Ví dụ chạy được**: Tất cả examples có `serve_rust.py` (01-12) đều chạy được ở Rust mode.

> **Shorthand syntax:** Các ví dụ trong chương này sử dụng shorthand syntax cho gọn.
> Xem [Shorthand Reference](12-shorthand-syntax.md) để biết đầy đủ.
>
> | Syntax | Class | Ví dụ |
> |--------|-------|-------|
> | `@op` | `FuncOp` | `@op` decorator trên function |
> | `@op(rust=...)` | `FuncOp` | Rust plugin op với Python fallback |

## Giới thiệu

**Rust mode** là execution backend thay thế cho Python mode mặc định. Thay vì dùng Python asyncio để schedule ops, Rust mode dùng **hush-icore** (viết bằng Rust) với:

- **DashMap** cho concurrent state — thread-safe, lock-free reads
- **rayon** cho parallel execution — fan-out/fan-in chạy thật song song
- **tokio** cho async I/O — LLM calls, embeddings chạy concurrent

## Cài đặt hush-icore

```bash
cd rust && cargo build --release
```

> **Lưu ý:** Cần Rust toolchain (`rustup`).

## Sử dụng Rust mode

Dùng `engine.serve(backend="rust")` để chạy workflow trên Rust backend (hush-serve + Axum):

```python
from hush.core import Hush, GraphOp, op, START, END, PARENT

@op(rust="./rust_ops::math::double")
def double(x: int):
    return {"result": x * 2}

with GraphOp(name="demo") as graph:
    step = double(x=PARENT["x"])
    START >> step >> END

engine = Hush(graph)

# Python backend (mặc định) — FastAPI + uvicorn
engine.serve(port=8000)

# Rust backend — Axum + hush-serve
engine.serve(port=8000, backend="rust", rust_ops="rust_ops")
```

> **Lưu ý:** `backend="rust"` yêu cầu hush-serve binary đã build (`cd rust && cargo build --release -p hush-serve`) và plugin cdylib đã build.

### Parallel execution trong Rust mode

Rust mode sử dụng **batch-aware scheduler**:
- Khi nhiều ops ready cùng lúc → chạy song song qua rayon/tokio
- Python ops: release GIL, chạy song song nếu I/O-bound
- Rust plugin ops: chạy hoàn toàn ngoài GIL
- State dùng DashMap (concurrent HashMap) — thread-safe

## Rust Plugin Ops

Để dùng Rust mode, **mọi `@op` / FuncOp trong graph đều phải có Rust version** — nếu bất kỳ op nào thiếu `rust="..."`, Rust mode sẽ không thể kích hoạt. Rust plugin ops được compile thành shared library (.so/.dylib/.dll) và load tại runtime qua **cdylib plugin system**.

### Tại sao cần Rust plugin ops?

- **Bắt buộc cho Rust mode** — mọi custom op phải có Rust implementation
- **CPU-bound tasks** — hash chains, data processing, tính toán nặng chạy ngoài GIL
- **Python fallback** — cùng function body vẫn chạy được ở Python mode khi không cần Rust

### Cách sử dụng

Dùng `@op(rust="<path>::<func>")` decorator:

```python
# Plugin op — path tương đối đến crate::module::function
@op(rust="./rust_ops::pipeline::my_function")
def my_function(x: int):
    return {"result": x + 1}  # Python fallback

# Hoặc chỉ function name nếu crate mặc định
@op(rust="./rust_ops::math::double")
def double(x: int):
    return {"result": x * 2}
```

Tất cả Rust ops đều được dispatch qua `OpRegistry` trait — không có built-in ops riêng biệt. Mọi custom op phải được viết trong một cdylib plugin crate.

## Tạo Rust Plugin (cdylib)

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
hush-plugin = { version = "0.1.0" }  # Plugin SDK
serde_json = "1"
```

> **Quan trọng:** `crate-type = ["cdylib"]` là bắt buộc. Nếu thiếu, library sẽ không load được.

### Bước 3: Viết ops (src/lib.rs)

```rust
use hush_plugin::{hush_plugin, serde_json};
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

// Export via OpRegistry trait — hush-serve loads at runtime
hush_plugin!(multiply, uppercase);
```

**Quy tắc:**
- Signature: `fn(&serde_json::Value) -> serde_json::Value`
- Input: JSON object, đọc fields bằng `inputs["key"]`
- Output: JSON object, thường là `{"result": value}` hoặc `{"error": msg}`
- `hush_plugin!` macro tạo `OpRegistry` implementation, export qua C ABI

### Bước 4: Build và load

```bash
cd my-ops && cargo build --release
```

Plugin được load tự động bởi `hush-serve` khi chỉ định `--plugin`:

```bash
hush-serve --plugin ./target/release/libmy_ops.so
```

Hoặc trong Python, `_rust_bridge.py` tự detect và pass `--plugin` khi spawning hush-serve.

### Bước 5: Sử dụng trong Python

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

    engine = Hush(graph)

    # Rust backend — dùng compiled plugin
    engine.serve(port=8000, backend="rust", rust_ops="my-ops")

    # Python backend — dùng Python fallback body
    engine.serve(port=8000)
```

## Plugin System Architecture

```
hush-plugin crate
├── OpRegistry trait       # Interface cho plugin ops
├── hush_plugin! macro     # Auto-generate registry + C ABI exports
└── C ABI functions        # rush_create_registry(), rush_destroy_registry()

hush-serve
├── --plugin flag          # Load .so/.dylib/.dll at runtime
├── libloading             # Dynamic library loading
└── OpRegistry dispatch    # Route ops to plugin functions

_rust_bridge.py
├── Auto-detect plugins    # Scan for cdylib crates in workspace
└── --plugin passthrough   # Pass plugin paths to hush-serve
```

## Mọi op đều phải có Rust version

Khi dùng `backend="rust"`, **tất cả** `@op` trong graph phải có `rust="..."`. Nếu thiếu bất kỳ op nào, Rust mode sẽ không kích hoạt được:

```python
@op(rust="./rust_ops::io::fetch_data")
def fetch_data():
    """I/O bound — Python fallback cũng chạy được."""
    return {"data": [1, 2, 3, 4, 5]}

@op(rust="./rust_ops::math::sum_values")
def sum_values(values: list):
    """CPU bound — chạy ngoài GIL trong Rust mode."""
    return {"result": sum(values)}

with GraphOp(name="mixed") as graph:
    f = fetch_data()
    s = sum_values(values=f["data"])
    START >> f >> s >> END

engine = Hush(graph)
# Rust backend — cả 2 ops đều có rust version → OK
engine.serve(port=8000, backend="rust", rust_ops="rust_ops")
# Python backend — dùng Python fallback body
engine.serve(port=8000)
```

## Bound hints (`bound="io"` / `bound="cpu"`)

Scheduler hint cho Rust mode biết cách schedule op:

```python
@op(bound="io")
async def call_api(url: str):
    """I/O-bound: schedule qua tokio async runtime."""
    ...

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

- **Ref.apply() không hỗ trợ Rust mode**: Python callables không thể cross FFI boundary. Dùng `@op(rust="...")` thay thế. `_serialize_transforms()` raise `ValueError` rõ ràng khi serialize.
- **Async ops** chạy sync-style trong Rust scheduler (tokio handles concurrency)
- **Streaming** hỗ trợ cho LLM ops, nhưng chưa hỗ trợ cho custom plugin ops
- Cần **Rust toolchain** để build plugins (`cargo` trong PATH)

## Tiếp theo

- [Parallel Execution](08-parallel-execution.md) — Fan-out/fan-in, generator iteration
- [Tracing & Observability](09-tracing-observability.md) — Debug workflows (hỗ trợ cả Rust mode)
