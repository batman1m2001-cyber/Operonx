# hush-plugin

Plugin SDK for writing custom Rust ops that load into hush-serve at runtime.

[![crates.io](https://img.shields.io/crates/v/hush-plugin)](https://crates.io/crates/hush-plugin)

## Quick Start

Create a cdylib crate:

```toml
# Cargo.toml
[lib]
crate-type = ["cdylib"]

[dependencies]
hush-plugin = "0.1"
```

Write your ops:

```rust
use hush_plugin::{hush_plugin, serde_json};
use serde_json::Value;

fn double(inputs: &Value) -> Value {
    let x = inputs["x"].as_i64().unwrap();
    serde_json::json!({"result": x * 2})
}

fn range_gen(inputs: &Value) -> Vec<Value> {
    let n = inputs["n"].as_i64().unwrap_or(3);
    (0..n).map(|i| serde_json::json!({"value": i})).collect()
}

hush_plugin!(double, range_gen);
```

Reference from Python:

```python
@op(rust="./my_crate::double")
def double(x: int):
    return {"result": x * 2}  # Python fallback
```

## Op Signatures

- **Regular op**: `fn(&Value) -> Value`
- **Generator op**: `fn(&Value) -> Vec<Value>`

The `hush_plugin!` macro auto-generates the C ABI exports and `OpRegistry` implementation.

## License

Apache 2.0
