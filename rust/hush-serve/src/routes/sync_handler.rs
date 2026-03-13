//! POST /path — synchronous JSON request/response.

use serde_json::Value;

/// Remove internal $-prefixed keys from the result.
pub fn filter_internal_keys(value: Value) -> Value {
    match value {
        Value::Object(map) => {
            let filtered: serde_json::Map<String, Value> = map
                .into_iter()
                .filter(|(k, _)| !k.starts_with('$'))
                .collect();
            Value::Object(filtered)
        }
        other => other,
    }
}
