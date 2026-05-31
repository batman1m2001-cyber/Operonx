//! `TritonOp` — Triton Inference Server gRPC client op.
//!
//! Mirrors Python [`operonx/providers/ops/triton.py`](../../../../../operonx/providers/ops/triton.py)
//! using `tonic` + `prost` instead of Python's `tritonclient.grpc.aio`. The
//! generated client + message types come from `proto/triton/grpc_service.proto`
//! (vendored from triton-inference-server/common) — see `build.rs`.
//!
//! Enabled behind the `triton` cargo feature so tier-1 lean builds don't
//! need `protoc` on the host.

#[cfg(not(feature = "triton"))]
use crate::core::configs::op_config::OpConfig;
#[cfg(not(feature = "triton"))]
use crate::core::exceptions::OperonError;
#[cfg(not(feature = "triton"))]
use serde_json::{Map, Value};

/// Execute a Triton inference op. Errors at runtime if the crate was built
/// without `--features triton`.
#[cfg(not(feature = "triton"))]
pub async fn execute(op: &OpConfig, _inputs: Map<String, Value>) -> Result<Value, OperonError> {
    Err(OperonError::Provider(format!(
        "operonx built without the `triton` feature — rebuild with --features triton (op: {})",
        op.full_name
    )))
}

#[cfg(feature = "triton")]
pub use triton_impl::execute;

#[cfg(feature = "triton")]
mod triton_impl {
    use std::collections::{BTreeMap, HashMap};
    use std::sync::{Mutex, OnceLock};
    use std::time::Duration;

    use serde_json::{json, Map, Value};
    use tonic::transport::{Channel, Endpoint};

    use crate::core::configs::op_config::OpConfig;
    use crate::core::exceptions::OperonError;

    // Generated client + message types — see `build.rs` and
    // `proto/triton/grpc_service.proto`.
    pub mod proto {
        tonic::include_proto!("inference");
    }

    use proto::grpc_inference_service_client::GrpcInferenceServiceClient;
    use proto::model_infer_request::{InferInputTensor, InferRequestedOutputTensor};
    use proto::{InferTensorContents, ModelInferRequest, ModelInferResponse};

    /// Per-process channel pool keyed by URL. The Python side caches one
    /// `InferenceServerClient` per URL; we keep a `Channel` so cloning it
    /// for each call is cheap (Channel is internally arc'd by tonic).
    fn channel_pool() -> &'static Mutex<HashMap<String, Channel>> {
        static POOL: OnceLock<Mutex<HashMap<String, Channel>>> = OnceLock::new();
        POOL.get_or_init(|| Mutex::new(HashMap::new()))
    }

    fn get_channel(url: &str) -> Result<Channel, OperonError> {
        if let Some(ch) = channel_pool().lock().unwrap().get(url).cloned() {
            return Ok(ch);
        }
        let endpoint_str = if url.starts_with("http://") || url.starts_with("https://") {
            url.to_string()
        } else {
            format!("http://{}", url)
        };
        let endpoint = Endpoint::from_shared(endpoint_str)
            .map_err(|e| OperonError::Provider(format!("triton endpoint parse: {}", e)))?
            .connect_timeout(Duration::from_secs(5))
            .timeout(Duration::from_secs(60));
        // `connect_lazy` returns a Channel that dials on first RPC — matches
        // Python's lazy-on-first-infer behavior and keeps `execute()`
        // non-blocking on cold starts.
        let channel = endpoint.connect_lazy();
        channel_pool()
            .lock()
            .unwrap()
            .insert(url.to_string(), channel.clone());
        Ok(channel)
    }

    /// Execute a Triton inference op.
    ///
    /// Reads `op.resource` (e.g. `"stt"`) → looks up `triton:stt` in the
    /// ResourceHub → extracts `url`, `model`, `model_version`, optional
    /// `inputs_map` / `outputs_map`. Direct inline configs (e.g.
    /// `inputs_map` set on the op itself) override the resource defaults.
    pub async fn execute(op: &OpConfig, inputs: Map<String, Value>) -> Result<Value, OperonError> {
        let cfg = TritonExecConfig::from_op_config(op)?;
        let channel = get_channel(&cfg.url)?;
        let mut client = GrpcInferenceServiceClient::new(channel);

        // Build the request.
        let mut triton_inputs: Vec<InferInputTensor> = Vec::with_capacity(cfg.inputs_map.len());
        for (triton_name, op_name) in &cfg.inputs_map {
            let Some(value) = inputs.get(op_name) else {
                continue;
            };
            let (datatype, shape, contents) = value_to_tensor(value, cfg.input_dtype(triton_name))?;
            triton_inputs.push(InferInputTensor {
                name: triton_name.clone(),
                datatype,
                shape,
                parameters: HashMap::new(),
                contents: Some(contents),
            });
        }

        let triton_outputs: Vec<InferRequestedOutputTensor> = cfg
            .outputs_map
            .iter()
            .map(|(triton_name, _)| InferRequestedOutputTensor {
                name: triton_name.clone(),
                parameters: HashMap::new(),
            })
            .collect();

        let request = tonic::Request::new(ModelInferRequest {
            model_name: cfg.model_name.clone(),
            model_version: cfg.model_version.clone(),
            id: String::new(),
            parameters: HashMap::new(),
            inputs: triton_inputs,
            outputs: triton_outputs,
            raw_input_contents: Vec::new(),
        });

        let response: ModelInferResponse = client
            .model_infer(request)
            .await
            .map_err(|e| {
                OperonError::Provider(format!(
                    "triton infer failed (model={}): {}",
                    cfg.model_name, e
                ))
            })?
            .into_inner();

        // Map outputs back.
        let mut out = Map::with_capacity(cfg.outputs_map.len());
        for (triton_name, op_name) in &cfg.outputs_map {
            let Some(tensor) = response.outputs.iter().find(|t| t.name == *triton_name) else {
                out.insert(op_name.clone(), Value::Null);
                continue;
            };
            out.insert(op_name.clone(), tensor_to_value(tensor));
        }
        Ok(Value::Object(out))
    }

    struct TritonExecConfig {
        url: String,
        model_name: String,
        model_version: String,
        inputs_map: Vec<(String, String)>,
        outputs_map: Vec<(String, String)>,
        input_dtypes: HashMap<String, String>,
    }

    impl TritonExecConfig {
        fn from_op_config(op: &OpConfig) -> Result<Self, OperonError> {
            // Resource may be a string (hub lookup) or a dict (inline config).
            // Matches Python's TritonOp.__init__ branching.
            let (url, model_name, model_version, hub_inputs, hub_outputs, hub_dtypes) =
                match &op.resource {
                    Some(Value::String(s)) => resolve_from_hub(s, &op.full_name)?,
                    Some(Value::Object(m)) => resolve_inline(m, &op.full_name)?,
                    _ => return Err(OperonError::Config(format!(
                        "TritonOp '{}' missing `resource` (string for hub lookup or dict for inline config)",
                        op.full_name
                    ))),
                };
            // Inline op.inputs_map / op.outputs_map override the resource's
            // defaults — Python's _process does the same merge.
            let inputs_map = if !op.inputs_map.is_empty() {
                btree_to_vec(&op.inputs_map)
            } else {
                hub_inputs
            };
            let outputs_map = if !op.outputs_map.is_empty() {
                btree_to_vec(&op.outputs_map)
            } else {
                hub_outputs
            };
            Ok(Self {
                url,
                model_name,
                model_version,
                inputs_map,
                outputs_map,
                input_dtypes: hub_dtypes,
            })
        }

        fn input_dtype(&self, triton_name: &str) -> Option<&str> {
            self.input_dtypes.get(triton_name).map(String::as_str)
        }
    }

    fn btree_to_vec(m: &BTreeMap<String, String>) -> Vec<(String, String)> {
        m.iter().map(|(k, v)| (k.clone(), v.clone())).collect()
    }

    type ResolvedConfig = (
        String,                  // url
        String,                  // model_name
        String,                  // model_version
        Vec<(String, String)>,   // inputs_map
        Vec<(String, String)>,   // outputs_map
        HashMap<String, String>, // input_dtypes (optional override)
    );

    fn resolve_from_hub(key: &str, op_name: &str) -> Result<ResolvedConfig, OperonError> {
        use crate::core::registry::ResourceHub;
        let hub = ResourceHub::instance()?;
        let lookup_key = if key.contains(':') {
            key.to_string()
        } else {
            format!("triton:{}", key)
        };
        let cfg = hub.get_config(&lookup_key).map_err(|e| {
            OperonError::ResourceHub(format!(
                "TritonOp '{}' resource lookup failed for '{}': {}",
                op_name, lookup_key, e
            ))
        })?;
        // ConfigDict is a serde_json Map under the hood — Triton resources
        // serialize as plain `{url, model, model_version, inputs_map,
        // outputs_map, input_dtypes?}` per resources.yaml.
        let v: Value = serde_json::to_value(&cfg)
            .map_err(|e| OperonError::ResourceHub(format!("config serialize: {}", e)))?;
        let obj = v.as_object().ok_or_else(|| {
            OperonError::ResourceHub(format!(
                "TritonOp '{}' resource '{}' is not a dict",
                op_name, lookup_key
            ))
        })?;
        resolve_inline(obj, op_name)
    }

    fn resolve_inline(
        obj: &Map<String, Value>,
        op_name: &str,
    ) -> Result<ResolvedConfig, OperonError> {
        let url = obj
            .get("url")
            .and_then(|v| v.as_str())
            .ok_or_else(|| {
                OperonError::Config(format!("TritonOp '{}' resource missing `url`", op_name))
            })?
            .to_string();
        let model_name = obj
            .get("model")
            .or_else(|| obj.get("model_name"))
            .and_then(|v| v.as_str())
            .ok_or_else(|| {
                OperonError::Config(format!(
                    "TritonOp '{}' resource missing `model` / `model_name`",
                    op_name
                ))
            })?
            .to_string();
        let model_version = obj
            .get("model_version")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string();
        let inputs_map = read_string_map_pairs(obj.get("inputs_map"));
        let outputs_map = read_string_map_pairs(obj.get("outputs_map"));
        let input_dtypes = read_string_map(obj.get("input_dtypes"));
        Ok((
            url,
            model_name,
            model_version,
            inputs_map,
            outputs_map,
            input_dtypes,
        ))
    }

    fn read_string_map_pairs(v: Option<&Value>) -> Vec<(String, String)> {
        v.and_then(|x| x.as_object())
            .map(|obj| {
                obj.iter()
                    .filter_map(|(k, v)| v.as_str().map(|s| (k.clone(), s.to_string())))
                    .collect()
            })
            .unwrap_or_default()
    }

    fn read_string_map(v: Option<&Value>) -> HashMap<String, String> {
        v.and_then(|x| x.as_object())
            .map(|obj| {
                obj.iter()
                    .filter_map(|(k, v)| v.as_str().map(|s| (k.clone(), s.to_string())))
                    .collect()
            })
            .unwrap_or_default()
    }

    /// Convert a JSON `Value` into a Triton tensor `(datatype, shape, contents)`.
    /// Defaults: arrays → FP32 (length-1 shape), strings → BYTES, numbers → FP32.
    /// The user can override via `input_dtypes` in the resource config.
    fn value_to_tensor(
        value: &Value,
        dtype_override: Option<&str>,
    ) -> Result<(String, Vec<i64>, InferTensorContents), OperonError> {
        match value {
            Value::Array(arr) => {
                let dtype = dtype_override.unwrap_or("FP32");
                let shape = vec![arr.len() as i64];
                let contents = encode_array(arr, dtype)?;
                Ok((dtype.to_string(), shape, contents))
            }
            Value::String(s) => {
                let dtype = dtype_override.unwrap_or("BYTES");
                let contents = InferTensorContents {
                    bytes_contents: vec![s.as_bytes().to_vec()],
                    ..Default::default()
                };
                Ok((dtype.to_string(), vec![1], contents))
            }
            Value::Number(n) => {
                let dtype = dtype_override.unwrap_or("FP32");
                let v = Value::Number(n.clone());
                let contents = encode_array(std::slice::from_ref(&v), dtype)?;
                Ok((dtype.to_string(), vec![1], contents))
            }
            other => Err(OperonError::Config(format!(
                "TritonOp input must be array / number / string; got {:?}",
                other
            ))),
        }
    }

    fn encode_array(arr: &[Value], dtype: &str) -> Result<InferTensorContents, OperonError> {
        match dtype {
            "FP32" => {
                let mut out: Vec<f32> = Vec::with_capacity(arr.len());
                for v in arr {
                    out.push(v.as_f64().ok_or_else(|| {
                        OperonError::Config(format!("FP32 expects number, got {:?}", v))
                    })? as f32);
                }
                Ok(InferTensorContents {
                    fp32_contents: out,
                    ..Default::default()
                })
            }
            "FP64" => {
                let mut out: Vec<f64> = Vec::with_capacity(arr.len());
                for v in arr {
                    out.push(v.as_f64().ok_or_else(|| {
                        OperonError::Config(format!("FP64 expects number, got {:?}", v))
                    })?);
                }
                Ok(InferTensorContents {
                    fp64_contents: out,
                    ..Default::default()
                })
            }
            "INT32" => {
                let mut out: Vec<i32> = Vec::with_capacity(arr.len());
                for v in arr {
                    out.push(v.as_i64().ok_or_else(|| {
                        OperonError::Config(format!("INT32 expects integer, got {:?}", v))
                    })? as i32);
                }
                Ok(InferTensorContents {
                    int_contents: out,
                    ..Default::default()
                })
            }
            "INT64" => {
                let mut out: Vec<i64> = Vec::with_capacity(arr.len());
                for v in arr {
                    out.push(v.as_i64().ok_or_else(|| {
                        OperonError::Config(format!("INT64 expects integer, got {:?}", v))
                    })?);
                }
                Ok(InferTensorContents {
                    int64_contents: out,
                    ..Default::default()
                })
            }
            "BYTES" => {
                let mut out: Vec<Vec<u8>> = Vec::with_capacity(arr.len());
                for v in arr {
                    match v {
                        Value::String(s) => out.push(s.as_bytes().to_vec()),
                        _ => {
                            return Err(OperonError::Config(format!(
                                "BYTES expects string elements, got {:?}",
                                v
                            )))
                        }
                    }
                }
                Ok(InferTensorContents {
                    bytes_contents: out,
                    ..Default::default()
                })
            }
            other => Err(OperonError::Config(format!(
                "unsupported tensor dtype: {}",
                other
            ))),
        }
    }

    /// Convert a response tensor back to a JSON `Value`. String dtypes
    /// (BYTES) collapse to a `Value::String` for 1-element tensors and an
    /// array of strings otherwise; numeric dtypes always surface as a
    /// `Value::Array` of numbers.
    fn tensor_to_value(t: &proto::model_infer_response::InferOutputTensor) -> Value {
        let Some(contents) = &t.contents else {
            return Value::Null;
        };
        match t.datatype.as_str() {
            "FP32" => Value::Array(contents.fp32_contents.iter().map(|f| json!(*f)).collect()),
            "FP64" => Value::Array(contents.fp64_contents.iter().map(|f| json!(*f)).collect()),
            "INT32" => Value::Array(contents.int_contents.iter().map(|i| json!(*i)).collect()),
            "INT64" => Value::Array(contents.int64_contents.iter().map(|i| json!(*i)).collect()),
            "BYTES" => {
                let mut decoded: Vec<String> = Vec::with_capacity(contents.bytes_contents.len());
                for b in &contents.bytes_contents {
                    decoded.push(String::from_utf8_lossy(b).into_owned());
                }
                if decoded.len() == 1 {
                    Value::String(decoded.into_iter().next().unwrap())
                } else {
                    Value::Array(decoded.into_iter().map(Value::String).collect())
                }
            }
            _ => Value::Null,
        }
    }

    #[cfg(test)]
    mod tests {
        use super::*;
        use serde_json::json;

        #[test]
        fn value_to_tensor_fp32_array() {
            let v = json!([1.0, 2.0, 3.0]);
            let (dt, shape, contents) = value_to_tensor(&v, None).expect("encode");
            assert_eq!(dt, "FP32");
            assert_eq!(shape, vec![3]);
            assert_eq!(contents.fp32_contents, vec![1.0_f32, 2.0_f32, 3.0_f32]);
        }

        #[test]
        fn value_to_tensor_string_to_bytes() {
            let v = json!("hello");
            let (dt, _, contents) = value_to_tensor(&v, None).expect("encode");
            assert_eq!(dt, "BYTES");
            assert_eq!(contents.bytes_contents.len(), 1);
            assert_eq!(contents.bytes_contents[0], b"hello".to_vec());
        }

        #[test]
        fn value_to_tensor_int_array_with_override() {
            let v = json!([1, 2, 3]);
            let (dt, shape, contents) = value_to_tensor(&v, Some("INT32")).expect("encode");
            assert_eq!(dt, "INT32");
            assert_eq!(shape, vec![3]);
            assert_eq!(contents.int_contents, vec![1, 2, 3]);
        }

        #[test]
        fn tensor_to_value_bytes_single_decodes_to_string() {
            let t = proto::model_infer_response::InferOutputTensor {
                name: "TRANSCRIPT".into(),
                datatype: "BYTES".into(),
                shape: vec![1],
                parameters: HashMap::new(),
                contents: Some(InferTensorContents {
                    bytes_contents: vec![b"hello world".to_vec()],
                    ..Default::default()
                }),
            };
            assert_eq!(tensor_to_value(&t), Value::String("hello world".into()));
        }

        #[test]
        fn tensor_to_value_fp32_array_decodes_to_value_array() {
            let t = proto::model_infer_response::InferOutputTensor {
                name: "EMBEDDING".into(),
                datatype: "FP32".into(),
                shape: vec![3],
                parameters: HashMap::new(),
                contents: Some(InferTensorContents {
                    fp32_contents: vec![0.1, 0.2, 0.3],
                    ..Default::default()
                }),
            };
            let got = tensor_to_value(&t);
            assert!(got.is_array(), "want array, got {:?}", got);
            assert_eq!(got.as_array().unwrap().len(), 3);
        }
    }
}
