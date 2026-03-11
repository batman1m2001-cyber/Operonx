use serde::{Deserialize, Serialize};

// === Ingest Request ===

#[derive(Debug, Deserialize)]
pub struct IngestRequest {
    pub request_id: String,
    pub user_id: Option<String>,
    pub session_id: Option<String>,
    pub workflow_name: String,
    pub tags: Option<Vec<String>>,
    pub graph_structure: Vec<GraphNode>,
    pub records: Vec<Record>,
}

#[derive(Debug, Deserialize)]
pub struct GraphNode {
    pub op_name: String,
    pub op_type: Option<String>,
    pub parent_name: Option<String>,
    pub contain_generation: Option<bool>,
}

#[derive(Debug, Deserialize)]
pub struct Record {
    pub op_name: String,
    pub context_id: Option<String>,
    pub inputs: Option<serde_json::Value>,
    pub outputs: Option<serde_json::Value>,
    pub start_time: Option<String>,
    pub end_time: Option<String>,
    pub duration_ms: Option<f64>,
    pub model: Option<String>,
    pub usage: Option<Usage>,
    pub cost: Option<f64>,
    pub metadata: Option<serde_json::Value>,
}

#[derive(Debug, Deserialize)]
pub struct Usage {
    pub prompt_tokens: Option<i64>,
    pub completion_tokens: Option<i64>,
    pub total_tokens: Option<i64>,
}

// === Query Responses ===

#[derive(Debug, Serialize)]
pub struct IngestResponse {
    pub status: String,
    pub rows_inserted: usize,
}

#[derive(Debug, Serialize)]
pub struct TraceListResponse {
    pub traces: Vec<TraceSummary>,
    pub total: i64,
    pub page: i64,
}

#[derive(Debug, Serialize)]
pub struct TraceSummary {
    pub request_id: String,
    pub workflow_name: String,
    pub start_time: Option<String>,
    pub total_duration_ms: Option<f64>,
    pub prompt_tokens: i64,
    pub completion_tokens: i64,
    pub total_tokens: i64,
    pub total_cost: f64,
    pub node_count: i64,
    pub tags: Vec<String>,
    pub input_preview: String,
    pub output_preview: String,
}

#[derive(Debug, Serialize, Clone)]
pub struct TraceNode {
    pub id: i64,
    pub request_id: String,
    pub session_id: Option<String>,
    pub op_name: String,
    pub op_type: Option<String>,
    pub parent_name: Option<String>,
    pub context_id: Option<String>,
    pub execution_order: i64,
    pub start_time: Option<String>,
    pub end_time: Option<String>,
    pub duration_ms: Option<f64>,
    pub model: Option<String>,
    pub prompt_tokens: Option<i64>,
    pub completion_tokens: Option<i64>,
    pub total_tokens: Option<i64>,
    pub cost_usd: Option<f64>,
    pub input: Option<serde_json::Value>,
    pub output: Option<serde_json::Value>,
    pub contain_generation: bool,
    pub metadata: Option<serde_json::Value>,
    pub tags: Option<Vec<String>>,
    pub children: Vec<TraceNode>,
}

#[derive(Debug, Serialize)]
pub struct TraceDetailResponse {
    pub request_id: String,
    pub nodes: Vec<TraceNode>,
}

#[derive(Debug, Serialize)]
pub struct DbInfoResponse {
    pub path: String,
    pub exists: bool,
    pub size: u64,
}

#[derive(Debug, Serialize)]
pub struct StatusResponse {
    pub status: String,
}

// === Query Parameters ===

#[derive(Debug, Deserialize)]
pub struct TraceListParams {
    pub limit: Option<i64>,
    pub offset: Option<i64>,
    pub time_filter: Option<f64>,
}
