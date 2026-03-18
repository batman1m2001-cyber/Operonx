use hush_macros::hush_op;
use hush_serve::HushServer;
use serde_json::{json, Value};

/// Example op: double a number.
/// Python equivalent: @op def double(x: int): return {"result": x * 2}
#[hush_op]
fn double(x: i64) -> Value {
    json!({"result": x * 2})
}

/// Example generator: yield items from a list.
/// Python equivalent: @op def each_item(items: list): for item in items: yield {"value": item}
#[hush_op(generator)]
fn each_item(items: Vec<Value>) -> Value {
    let results: Vec<Value> = items
        .into_iter()
        .map(|item| json!({"value": item}))
        .collect();
    Value::Array(results)
}

fn main() {
    HushServer::builder()
        .auto_register()
        .from_cli()
        .serve()
        .unwrap();
}
