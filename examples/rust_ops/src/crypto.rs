//! Crypto ops — CPU-heavy hashing for benchmarking Rust vs Python.

use serde_json::{json, Value};
use std::collections::hash_map::DefaultHasher;
use std::hash::{Hash, Hasher};
use hush_serve::hush_op;

/// hash_chain: data, iterations → hash
///
/// Repeatedly hashes the input string. CPU-bound — demonstrates Rust's
/// performance advantage over Python for compute-heavy ops.
///
/// Python equivalent:
/// ```python
/// @op(rust="hash_chain")
/// def hash_chain(data: str, iterations: int):
///     import hashlib
///     current = data
///     for _ in range(iterations):
///         current = hashlib.md5(current.encode()).hexdigest()
///     return {"hash": current}
/// ```
#[hush_op]
pub fn hash_chain(inputs: &Value) -> Value {
    let data = inputs["data"].as_str().unwrap_or("");
    let iterations = inputs["iterations"].as_i64().unwrap_or(0);

    let mut current = data.to_string();
    for _ in 0..iterations {
        let mut hasher = DefaultHasher::new();
        current.hash(&mut hasher);
        current = format!("{:016x}", hasher.finish());
    }

    json!({"hash": current})
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_hash_chain() {
        let result = hash_chain(&json!({"data": "hello", "iterations": 3}));
        assert!(result["hash"].is_string());
        assert_eq!(result["hash"].as_str().unwrap().len(), 16);
    }

    #[test]
    fn test_hash_chain_deterministic() {
        let r1 = hash_chain(&json!({"data": "test", "iterations": 100}));
        let r2 = hash_chain(&json!({"data": "test", "iterations": 100}));
        assert_eq!(r1["hash"], r2["hash"]);
    }
}
