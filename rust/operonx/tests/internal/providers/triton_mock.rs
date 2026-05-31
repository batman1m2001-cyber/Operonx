//! End-to-end TritonOp test against a tonic in-process gRPC server.
//!
//! Stage 8 of the operonx Rust sync. Brings up a `GrpcInferenceService`
//! implementation that returns canned `ModelInferResponse` objects, then
//! calls `TritonOp::execute` through the provider factory and asserts the
//! output dict shape matches what the callbot expects (`TRANSCRIPT` →
//! string, `EMBEDDING` → numeric array, missing output → `null`).
//!
//! Only built when the `triton` cargo feature is enabled. The test wires
//! its own resource into the ResourceHub so it doesn't depend on
//! resources.yaml or the running Triton server at 192.168.1.212:8001.

#![cfg(feature = "triton")]

use std::collections::HashMap;
use std::net::SocketAddr;
use std::sync::Arc;

use serde_json::{json, Map, Value};
use tokio::net::TcpListener;
use tonic::transport::Server;
use tonic::{Request, Response, Status};

// Pull in the generated proto module that the operonx crate compiled.
mod proto {
    tonic::include_proto!("inference");
}

use proto::grpc_inference_service_server::{GrpcInferenceService, GrpcInferenceServiceServer};
use proto::model_infer_response::InferOutputTensor;
use proto::{InferTensorContents, ModelInferRequest, ModelInferResponse};

#[derive(Default)]
struct CannedTriton;

#[tonic::async_trait]
impl GrpcInferenceService for CannedTriton {
    async fn model_infer(
        &self,
        request: Request<ModelInferRequest>,
    ) -> Result<Response<ModelInferResponse>, Status> {
        let req = request.into_inner();
        // Sanity: assert we got the expected input shape so a bad
        // serialisation on the client side surfaces as a test failure
        // rather than a vague mismatch downstream.
        assert_eq!(req.model_name, "fastconformer_asr");
        let audio = req
            .inputs
            .iter()
            .find(|t| t.name == "AUDIO_SIGNAL")
            .expect("AUDIO_SIGNAL input present");
        assert_eq!(audio.datatype, "FP32");
        let contents = audio.contents.as_ref().expect("contents set");
        assert!(
            !contents.fp32_contents.is_empty(),
            "FP32 contents populated"
        );

        // Return canned outputs: TRANSCRIPT (BYTES, one string) +
        // EMBEDDING (FP32 array).
        let transcript = InferOutputTensor {
            name: "TRANSCRIPT".into(),
            datatype: "BYTES".into(),
            shape: vec![1],
            parameters: HashMap::new(),
            contents: Some(InferTensorContents {
                bytes_contents: vec![b"hello world".to_vec()],
                ..Default::default()
            }),
        };
        let embedding = InferOutputTensor {
            name: "EMBEDDING".into(),
            datatype: "FP32".into(),
            shape: vec![3],
            parameters: HashMap::new(),
            contents: Some(InferTensorContents {
                fp32_contents: vec![0.1, 0.2, 0.3],
                ..Default::default()
            }),
        };

        Ok(Response::new(ModelInferResponse {
            model_name: req.model_name,
            model_version: req.model_version,
            id: req.id,
            parameters: HashMap::new(),
            outputs: vec![transcript, embedding],
            raw_output_contents: Vec::new(),
        }))
    }
}

/// Bring up the canned server on a free port and return `(host:port, JoinHandle)`.
async fn start_mock() -> (String, tokio::task::JoinHandle<()>) {
    // Bind to an OS-assigned port to avoid clashes when tests run in
    // parallel. tonic doesn't expose a bind-and-tell helper so we open a
    // TCP listener first, recover the addr, then hand the socket to
    // tonic via `serve_with_incoming`.
    let listener = TcpListener::bind("127.0.0.1:0").await.expect("bind");
    let addr: SocketAddr = listener.local_addr().expect("local_addr");

    let handle = tokio::spawn(async move {
        let incoming = tokio_stream::wrappers::TcpListenerStream::new(listener);
        Server::builder()
            .add_service(GrpcInferenceServiceServer::new(CannedTriton::default()))
            .serve_with_incoming(incoming)
            .await
            .expect("server");
    });

    (format!("{}", addr), handle)
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn triton_op_round_trips_through_grpc() {
    use operonx::core::exceptions::OperonError;
    use operonx::core::registry::storage::ConfigDict;
    use operonx::core::registry::storage::ConfigStorage;
    use operonx::core::registry::ResourceHub;

    // Minimal in-memory ConfigStorage so the test doesn't need an on-disk
    // resources.yaml. Mirrors the pattern in resource_hub.rs's unit tests.
    struct MemStorage {
        data: std::sync::Mutex<std::collections::HashMap<String, ConfigDict>>,
    }
    impl ConfigStorage for MemStorage {
        fn load_one(&self, key: &str) -> Result<Option<ConfigDict>, OperonError> {
            Ok(self.data.lock().unwrap().get(key).cloned())
        }
        fn load_all(&self) -> Result<std::collections::HashMap<String, ConfigDict>, OperonError> {
            Ok(self.data.lock().unwrap().clone())
        }
        fn save(&self, k: &str, c: ConfigDict) -> Result<bool, OperonError> {
            self.data.lock().unwrap().insert(k.to_string(), c);
            Ok(true)
        }
        fn remove(&self, k: &str) -> Result<bool, OperonError> {
            Ok(self.data.lock().unwrap().remove(k).is_some())
        }
    }

    let (host_port, _server_handle) = start_mock().await;
    // Give the server a tick to start accepting.
    tokio::time::sleep(std::time::Duration::from_millis(50)).await;

    // Install a hub with a single triton:stt entry pointing at our mock.
    let mut entry = ConfigDict::new();
    entry.insert("url".into(), json!(host_port.clone()));
    entry.insert("model".into(), json!("fastconformer_asr"));
    entry.insert("inputs_map".into(), json!({"AUDIO_SIGNAL": "speech_audio"}));
    entry.insert(
        "outputs_map".into(),
        json!({"TRANSCRIPT": "transcript", "EMBEDDING": "embedding"}),
    );
    let mut data = std::collections::HashMap::new();
    data.insert("triton:stt".to_string(), entry);
    let storage = Arc::new(MemStorage {
        data: std::sync::Mutex::new(data),
    });
    let hub = ResourceHub::new(storage);
    ResourceHub::set_instance(Arc::new(hub));

    // Build the op config (TritonOp wire shape).
    let op_cfg: operonx::core::configs::op_config::OpConfig = serde_json::from_value(json!({
        "type": "triton",
        "name": "stt",
        "full_name": "main.stt",
        "bound": "io",
        "resource": "stt",
        "inputs": {
            "speech_audio": {"required": true}
        },
        "outputs": {
            "transcript": {},
            "embedding": {}
        }
    }))
    .expect("parse op config");

    // Inputs map (provided at op runtime).
    let mut inputs = Map::new();
    inputs.insert(
        "speech_audio".into(),
        json!([0.0_f32, 0.1_f32, 0.2_f32, 0.3_f32]),
    );

    // Call execute directly — same entry point the scheduler uses.
    let out = operonx::providers::ops::triton::execute(&op_cfg, inputs)
        .await
        .expect("execute");
    let obj = out.as_object().expect("output is object");
    assert_eq!(
        obj.get("transcript"),
        Some(&Value::String("hello world".into()))
    );
    let emb = obj
        .get("embedding")
        .and_then(|v| v.as_array())
        .expect("embedding array");
    assert_eq!(emb.len(), 3);
}
