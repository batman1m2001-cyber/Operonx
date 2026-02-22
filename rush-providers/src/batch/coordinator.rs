//! Batch coordinator — manages OpenAI Batch API lifecycle.
//!
//! Flow:
//! 1. submit(request) → adds to pending queue, returns a oneshot Receiver
//! 2. Auto-flush after flush_interval or when batch_size reached
//! 3. Flush: upload JSONL → create batch → poll until done → distribute results
//!
//! Mirrors hush-providers batch_coordinator.py with tokio async.

use std::collections::HashMap;
use std::sync::Arc;
use std::time::Duration;

use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use tokio::sync::{oneshot, Mutex};

use crate::config::llm::OpenAIConfig;
use crate::http::{get_client, ProviderError, ProviderResult};

/// A single batch request (one chat completion call).
struct PendingRequest {
    custom_id: String,
    body: Value,
    sender: oneshot::Sender<ProviderResult<Value>>,
}

/// Batch coordinator — accumulates requests and flushes as OpenAI Batch jobs.
pub struct BatchCoordinator {
    config: BatchConfig,
    state: Arc<Mutex<BatchState>>,
}

struct BatchState {
    pending: Vec<PendingRequest>,
    next_id: u64,
    flush_scheduled: bool,
}

/// Configuration for batch operations (extracted from OpenAIConfig).
#[derive(Clone)]
pub struct BatchConfig {
    pub api_key: String,
    pub base_url: String,
    pub model: String,
    pub max_batch_size: usize,
    pub flush_interval: Duration,
    pub poll_interval: Duration,
    pub timeout: Duration,
}

impl BatchConfig {
    pub fn from_openai_config(config: &OpenAIConfig) -> Self {
        BatchConfig {
            api_key: config.api_key.clone(),
            base_url: config.base_url.clone(),
            model: config.model.clone(),
            max_batch_size: config.batch_size,
            flush_interval: Duration::from_secs_f64(config.batch_flush_interval),
            poll_interval: Duration::from_secs_f64(config.batch_poll_interval),
            timeout: Duration::from_secs_f64(config.batch_timeout),
        }
    }
}

/// OpenAI Batch API request format (JSONL line).
#[derive(Serialize)]
struct BatchRequestLine {
    custom_id: String,
    method: String,
    url: String,
    body: Value,
}

/// OpenAI file upload response.
#[derive(Deserialize)]
struct FileUploadResponse {
    id: String,
}

/// OpenAI batch creation response.
#[derive(Deserialize)]
struct BatchResponse {
    id: String,
    status: String,
    output_file_id: Option<String>,
    error_file_id: Option<String>,
}

/// A single result line from the batch output file.
#[derive(Deserialize)]
struct BatchResultLine {
    custom_id: String,
    response: Option<BatchResultResponse>,
    error: Option<BatchResultError>,
}

#[derive(Deserialize)]
struct BatchResultResponse {
    status_code: u16,
    body: Value,
}

#[derive(Deserialize)]
struct BatchResultError {
    message: String,
    code: Option<String>,
}

impl BatchCoordinator {
    pub fn new(config: BatchConfig) -> Self {
        BatchCoordinator {
            config,
            state: Arc::new(Mutex::new(BatchState {
                pending: Vec::new(),
                next_id: 0,
                flush_scheduled: false,
            })),
        }
    }

    /// Submit a single chat completion request to the batch queue.
    /// Returns a future that resolves when the batch job completes.
    pub async fn submit(&self, messages: Value, params: Value) -> ProviderResult<Value> {
        let (sender, receiver) = oneshot::channel();

        let should_flush;
        let should_schedule;

        {
            let mut state = self.state.lock().await;
            let custom_id = format!("req-{}", state.next_id);
            state.next_id += 1;

            // Build the request body
            let mut body = json!({
                "model": &self.config.model,
                "messages": messages,
            });
            // Merge additional params
            if let Some(obj) = params.as_object() {
                for (k, v) in obj {
                    body[k] = v.clone();
                }
            }

            state.pending.push(PendingRequest {
                custom_id,
                body,
                sender,
            });

            should_flush = state.pending.len() >= self.config.max_batch_size;
            should_schedule = !state.flush_scheduled && !should_flush;

            if should_flush || should_schedule {
                state.flush_scheduled = true;
            }
        }

        if should_flush {
            self.flush().await;
        } else if should_schedule {
            // Schedule auto-flush after interval
            let this_state = Arc::clone(&self.state);
            let config = self.config.clone();
            tokio::spawn(async move {
                tokio::time::sleep(config.flush_interval).await;
                let mut state = this_state.lock().await;
                if !state.pending.is_empty() {
                    let requests = std::mem::take(&mut state.pending);
                    state.flush_scheduled = false;
                    drop(state);
                    process_batch(requests, &config).await;
                } else {
                    state.flush_scheduled = false;
                }
            });
        }

        // Wait for result
        receiver.await.map_err(|_| ProviderError {
            message: "Batch request channel closed unexpectedly".to_string(),
            status_code: None,
            error_code: None,
        })?
    }

    /// Flush all pending requests as a batch job.
    async fn flush(&self) {
        let requests;
        {
            let mut state = self.state.lock().await;
            requests = std::mem::take(&mut state.pending);
            state.flush_scheduled = false;
        }

        if requests.is_empty() {
            return;
        }

        let config = self.config.clone();
        tokio::spawn(async move {
            process_batch(requests, &config).await;
        });
    }
}

/// Process a batch of requests through OpenAI's Batch API.
async fn process_batch(requests: Vec<PendingRequest>, config: &BatchConfig) {
    // 1. Build JSONL
    let mut jsonl = String::new();
    let mut sender_map: HashMap<String, oneshot::Sender<ProviderResult<Value>>> = HashMap::new();

    for req in requests {
        let line = BatchRequestLine {
            custom_id: req.custom_id.clone(),
            method: "POST".to_string(),
            url: "/v1/chat/completions".to_string(),
            body: req.body,
        };
        if let Ok(json_str) = serde_json::to_string(&line) {
            jsonl.push_str(&json_str);
            jsonl.push('\n');
        }
        sender_map.insert(req.custom_id, req.sender);
    }

    // 2. Upload JSONL file
    let file_id = match upload_batch_file(config, &jsonl).await {
        Ok(id) => id,
        Err(e) => {
            let msg = format!("Batch file upload failed: {}", e);
            for (_, sender) in sender_map {
                let _ = sender.send(Err(ProviderError {
                    message: msg.clone(),
                    status_code: None,
                    error_code: None,
                }));
            }
            return;
        }
    };

    // 3. Create batch
    let batch_id = match create_batch(config, &file_id).await {
        Ok(id) => id,
        Err(e) => {
            let msg = format!("Batch creation failed: {}", e);
            for (_, sender) in sender_map {
                let _ = sender.send(Err(ProviderError {
                    message: msg.clone(),
                    status_code: None,
                    error_code: None,
                }));
            }
            return;
        }
    };

    // 4. Poll until completion
    let result = poll_batch(config, &batch_id).await;

    match result {
        Ok(output_file_id) => {
            // 5. Download and distribute results
            match download_results(config, &output_file_id).await {
                Ok(results) => {
                    for (custom_id, value) in results {
                        if let Some(sender) = sender_map.remove(&custom_id) {
                            let _ = sender.send(Ok(value));
                        }
                    }
                    // Any remaining senders → error
                    for (_, sender) in sender_map {
                        let _ = sender.send(Err(ProviderError {
                            message: "Batch result missing for this request".to_string(),
                            status_code: None,
                            error_code: None,
                        }));
                    }
                }
                Err(e) => {
                    let msg = format!("Batch result download failed: {}", e);
                    for (_, sender) in sender_map {
                        let _ = sender.send(Err(ProviderError {
                            message: msg.clone(),
                            status_code: None,
                            error_code: None,
                        }));
                    }
                }
            }
        }
        Err(e) => {
            let msg = format!("Batch processing failed: {}", e);
            for (_, sender) in sender_map {
                let _ = sender.send(Err(ProviderError {
                    message: msg.clone(),
                    status_code: None,
                    error_code: None,
                }));
            }
        }
    }
}

/// Upload a JSONL string as a file to OpenAI.
async fn upload_batch_file(config: &BatchConfig, jsonl: &str) -> ProviderResult<String> {
    let client = get_client();
    let url = format!("{}/files", config.base_url.trim_end_matches('/'));

    let form = reqwest::multipart::Form::new()
        .text("purpose", "batch")
        .part(
            "file",
            reqwest::multipart::Part::bytes(jsonl.as_bytes().to_vec())
                .file_name("batch_requests.jsonl")
                .mime_str("application/jsonl")
                .map_err(|e| ProviderError {
                    message: format!("Failed to create multipart: {}", e),
                    status_code: None,
                    error_code: None,
                })?,
        );

    let resp = client
        .post(&url)
        .header("Authorization", format!("Bearer {}", config.api_key))
        .multipart(form)
        .send()
        .await?;

    let status = resp.status();
    if !status.is_success() {
        let body = resp.text().await.unwrap_or_default();
        return Err(ProviderError {
            message: format!("File upload failed: {}", body),
            status_code: Some(status.as_u16()),
            error_code: None,
        });
    }

    let parsed: FileUploadResponse = resp.json().await.map_err(|e| ProviderError {
        message: format!("Failed to parse file upload response: {}", e),
        status_code: Some(status.as_u16()),
        error_code: None,
    })?;

    Ok(parsed.id)
}

/// Create a batch job on OpenAI.
async fn create_batch(config: &BatchConfig, input_file_id: &str) -> ProviderResult<String> {
    let client = get_client();
    let url = format!("{}/batches", config.base_url.trim_end_matches('/'));

    let body = json!({
        "input_file_id": input_file_id,
        "endpoint": "/v1/chat/completions",
        "completion_window": "24h",
    });

    let resp = client
        .post(&url)
        .header("Content-Type", "application/json")
        .header("Authorization", format!("Bearer {}", config.api_key))
        .json(&body)
        .send()
        .await?;

    let status = resp.status();
    if !status.is_success() {
        let body = resp.text().await.unwrap_or_default();
        return Err(ProviderError {
            message: format!("Batch creation failed: {}", body),
            status_code: Some(status.as_u16()),
            error_code: None,
        });
    }

    let parsed: BatchResponse = resp.json().await.map_err(|e| ProviderError {
        message: format!("Failed to parse batch response: {}", e),
        status_code: Some(status.as_u16()),
        error_code: None,
    })?;

    Ok(parsed.id)
}

/// Poll a batch job until it completes or times out.
async fn poll_batch(config: &BatchConfig, batch_id: &str) -> ProviderResult<String> {
    let client = get_client();
    let url = format!(
        "{}/batches/{}",
        config.base_url.trim_end_matches('/'),
        batch_id
    );

    let deadline = tokio::time::Instant::now() + config.timeout;

    loop {
        if tokio::time::Instant::now() >= deadline {
            return Err(ProviderError {
                message: format!(
                    "Batch {} timed out after {:?}",
                    batch_id, config.timeout
                ),
                status_code: None,
                error_code: None,
            });
        }

        tokio::time::sleep(config.poll_interval).await;

        let resp = client
            .get(&url)
            .header("Authorization", format!("Bearer {}", config.api_key))
            .send()
            .await?;

        let status = resp.status();
        if !status.is_success() {
            continue; // Retry on transient errors
        }

        let batch: BatchResponse = match resp.json().await {
            Ok(b) => b,
            Err(_) => continue,
        };

        match batch.status.as_str() {
            "completed" => {
                return batch.output_file_id.ok_or_else(|| ProviderError {
                    message: "Batch completed but no output_file_id".to_string(),
                    status_code: None,
                    error_code: None,
                });
            }
            "failed" | "expired" | "cancelled" => {
                return Err(ProviderError {
                    message: format!("Batch {} status: {}", batch_id, batch.status),
                    status_code: None,
                    error_code: None,
                });
            }
            _ => {
                // "validating", "in_progress", "finalizing" → keep polling
            }
        }
    }
}

/// Download and parse batch results from the output file.
async fn download_results(
    config: &BatchConfig,
    output_file_id: &str,
) -> ProviderResult<Vec<(String, Value)>> {
    let client = get_client();
    let url = format!(
        "{}/files/{}/content",
        config.base_url.trim_end_matches('/'),
        output_file_id
    );

    let resp = client
        .get(&url)
        .header("Authorization", format!("Bearer {}", config.api_key))
        .send()
        .await?;

    let status = resp.status();
    if !status.is_success() {
        let body = resp.text().await.unwrap_or_default();
        return Err(ProviderError {
            message: format!("Failed to download batch results: {}", body),
            status_code: Some(status.as_u16()),
            error_code: None,
        });
    }

    let text = resp.text().await.map_err(|e| ProviderError {
        message: format!("Failed to read batch results: {}", e),
        status_code: None,
        error_code: None,
    })?;

    let mut results = Vec::new();
    for line in text.lines() {
        if line.trim().is_empty() {
            continue;
        }
        let parsed: BatchResultLine = serde_json::from_str(line).map_err(|e| ProviderError {
            message: format!("Failed to parse batch result line: {}", e),
            status_code: None,
            error_code: None,
        })?;

        let value = if let Some(resp) = parsed.response {
            if resp.status_code == 200 {
                // Parse as ChatCompletion response
                crate::http::llm::format_batch_result(&resp.body)
            } else {
                json!({
                    "error_code": resp.status_code,
                    "error_message": resp.body.to_string(),
                    "content": "",
                    "role": "assistant",
                })
            }
        } else if let Some(err) = parsed.error {
            json!({
                "error_message": err.message,
                "error_code": err.code,
                "content": "",
                "role": "assistant",
            })
        } else {
            json!({
                "error_message": "Unknown batch result format",
                "content": "",
                "role": "assistant",
            })
        };

        results.push((parsed.custom_id, value));
    }

    Ok(results)
}
