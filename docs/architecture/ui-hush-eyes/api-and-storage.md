# Hush Eyes — API and Storage

## REST API

All API endpoints are served under `/api/`. The web UI is served at the root path via static file fallback.

### POST /api/ingest

Receive trace data from `HushEyesTracer` and insert into SQLite.

**Request body** (`IngestRequest`):

```json
{
  "request_id": "req_abc123",
  "workflow_name": "my_workflow",
  "user_id": "user_123",
  "session_id": "session_456",
  "tags": ["dev", "experiment-v2"],
  "graph_structure": [
    {
      "op_name": "my_workflow",
      "op_type": "GraphOp",
      "parent_name": null,
      "contain_generation": false
    },
    {
      "op_name": "my_workflow.llm_step",
      "op_type": "LLMOp",
      "parent_name": "my_workflow",
      "contain_generation": true
    }
  ],
  "records": [
    {
      "op_name": "my_workflow",
      "context_id": null,
      "inputs": {"query": "hello"},
      "outputs": {"answer": "world"},
      "start_time": "2026-02-16T10:00:00.000Z",
      "end_time": "2026-02-16T10:00:01.500Z",
      "duration_ms": 1500.0,
      "model": null,
      "usage": null,
      "cost": null,
      "metadata": null
    },
    {
      "op_name": "my_workflow.llm_step",
      "context_id": null,
      "inputs": {"messages": [{"role": "user", "content": "hello"}]},
      "outputs": {"content": "world"},
      "start_time": "2026-02-16T10:00:00.100Z",
      "end_time": "2026-02-16T10:00:01.400Z",
      "duration_ms": 1300.0,
      "model": "gpt-4o",
      "usage": {
        "prompt_tokens": 10,
        "completion_tokens": 5,
        "total_tokens": 15
      },
      "cost": 0.00035,
      "metadata": {"temperature": 0.7}
    }
  ]
}
```

**Response** (`IngestResponse`):

```json
{
  "status": "ok",
  "rows_inserted": 2
}
```

**Processing logic** (`write.rs`):
1. Build a lookup map from `graph_structure` (op_name -> static metadata like `op_type`, `parent_name`, `contain_generation`)
2. Serialize `tags` to JSON string
3. Open an `unchecked_transaction` for batch insert performance
4. For each record in `records`:
   - Merge static metadata from `graph_structure` with dynamic data from the record
   - `parent_name` is resolved from: record metadata first, then `graph_structure` fallback
   - `execution_order` is the record's index position (0, 1, 2, ...)
   - `created_at` is set to the current Unix timestamp (seconds as f64)
   - `status` is always `'flushed'`
5. Commit the transaction

### GET /api/traces

List trace summaries, grouped by `request_id`, with pagination and optional time filtering.

**Query parameters** (`TraceListParams`):

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `limit` | i64 | 50 | Maximum number of traces to return |
| `offset` | i64 | 0 | Number of traces to skip |
| `time_filter` | f64 | - | Only return traces created within the last N seconds |

**Response** (`TraceListResponse`):

```json
{
  "traces": [
    {
      "request_id": "req_abc123",
      "workflow_name": "my_workflow",
      "start_time": "2026-02-16T10:00:00.000Z",
      "total_duration_ms": 1500.0,
      "prompt_tokens": 10,
      "completion_tokens": 5,
      "total_tokens": 15,
      "total_cost": 0.00035,
      "node_count": 2,
      "tags": ["dev"],
      "input_preview": "hello",
      "output_preview": "world"
    }
  ],
  "total": 1,
  "page": 1
}
```

**Aggregation logic** (`read.rs`):
- Groups rows by `request_id`
- `total_duration_ms`: maximum `duration_ms` among root nodes (where `parent_name IS NULL`)
- Token and cost sums: only from rows where `contain_generation = 1`
- `input_preview` / `output_preview`: extracted from root node's `input`/`output` fields using smart preview logic (looks for common keys like `text`, `message`, `content`, `query`, etc., truncated to 200 chars)

### GET /api/traces/{request_id}

Get full trace detail with hierarchical tree structure.

**Response** (`TraceDetailResponse`):

```json
{
  "request_id": "req_abc123",
  "nodes": [
    {
      "id": 1,
      "request_id": "req_abc123",
      "session_id": "session_456",
      "op_name": "my_workflow",
      "op_type": "GraphOp",
      "parent_name": null,
      "context_id": null,
      "execution_order": 0,
      "start_time": "2026-02-16T10:00:00.000Z",
      "end_time": "2026-02-16T10:00:01.500Z",
      "duration_ms": 1500.0,
      "model": null,
      "prompt_tokens": null,
      "completion_tokens": null,
      "total_tokens": null,
      "cost_usd": null,
      "input": {"query": "hello"},
      "output": {"answer": "world"},
      "contain_generation": false,
      "metadata": null,
      "tags": ["dev"],
      "children": [
        {
          "id": 2,
          "op_name": "my_workflow.llm_step",
          "parent_name": "my_workflow",
          "children": [],
          "..."
        }
      ]
    }
  ]
}
```

**Tree building** (`read.rs: build_tree()`):
1. Query all rows for the `request_id`, ordered by `execution_order`
2. Build lookup maps: `node_by_key` (op_name:context_id -> index) and `nodes_by_name` (op_name -> list of indices)
3. Resolve parent-child relationships using four strategies (in order):
   - **Strategy 1**: Parent with parent context (drop last `.[N]` segment from child's `context_id`)
   - **Strategy 2**: Parent with same `context_id`
   - **Strategy 3**: Parent without context (unique name match)
   - **Strategy 4**: Multi-candidate fallback with context prefix matching
4. Sort children by `execution_order`
5. Recursively build tree from root nodes (nodes with no resolved parent)

### DELETE /api/traces/{request_id}

Delete all rows for a specific `request_id`.

**Response** (`StatusResponse`):

```json
{"status": "ok"}
```

### DELETE /api/traces

Clear all traces from the database.

**Response** (`StatusResponse`):

```json
{"status": "ok"}
```

### GET /api/db-info

Get database file information.

**Response** (`DbInfoResponse`):

```json
{
  "path": "/home/user/.hush/traces.db",
  "exists": true,
  "size": 1048576
}
```

## Static File Serving

Static files from the `static/` directory are served at the root path using `tower-http::services::ServeDir`. The API routes (`/api/*`) take priority over static file routes via Axum's fallback mechanism.

```
GET /              → static/index.html
GET /main.js       → static/main.js
GET /styles.css    → static/styles.css
GET /api/traces    → query::list_traces handler
```

The static directory path is resolved at compile time using `env!("CARGO_MANIFEST_DIR")`.

## CORS

The server enables permissive CORS via `tower-http::cors::CorsLayer`:
- `allow_origin`: Any
- `allow_methods`: Any
- `allow_headers`: Any

This allows the web UI and external tools to access the API without restrictions.

## SQLite Schema

### traces Table

```sql
CREATE TABLE IF NOT EXISTS traces (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT NOT NULL,
    workflow_name TEXT NOT NULL,

    op_name TEXT,
    op_type TEXT,
    parent_name TEXT,
    context_id TEXT,
    execution_order INTEGER,

    start_time TEXT,
    end_time TEXT,
    duration_ms REAL,

    model TEXT,
    prompt_tokens INTEGER,
    completion_tokens INTEGER,
    total_tokens INTEGER,
    cost_usd REAL,

    input TEXT,
    output TEXT,

    user_id TEXT,
    session_id TEXT,
    contain_generation INTEGER DEFAULT 0,
    metadata TEXT,
    tags TEXT,

    status TEXT DEFAULT 'flushed',
    created_at REAL NOT NULL
);
```

### Column Descriptions

| Column | Type | Description |
|--------|------|-------------|
| `id` | INTEGER | Auto-incrementing primary key |
| `request_id` | TEXT | Groups all ops from a single `engine.run()` call |
| `workflow_name` | TEXT | Name of the root GraphOp |
| `op_name` | TEXT | Fully qualified op name (e.g., `workflow.step1`) |
| `op_type` | TEXT | Op class name (e.g., `GraphOp`, `LLMOp`, `FuncOp`) |
| `parent_name` | TEXT | Parent op's name (null for root) |
| `context_id` | TEXT | Iteration context (e.g., `[0]`, `[0].[1]` for nested loops) |
| `execution_order` | INTEGER | Position in the execution sequence (0-indexed) |
| `start_time` | TEXT | ISO 8601 timestamp when op started |
| `end_time` | TEXT | ISO 8601 timestamp when op finished |
| `duration_ms` | REAL | Execution duration in milliseconds |
| `model` | TEXT | LLM model name (e.g., `gpt-4o`) |
| `prompt_tokens` | INTEGER | Input token count (LLM ops only) |
| `completion_tokens` | INTEGER | Output token count (LLM ops only) |
| `total_tokens` | INTEGER | Total token count |
| `cost_usd` | REAL | Estimated cost in USD |
| `input` | TEXT | JSON-serialized input data |
| `output` | TEXT | JSON-serialized output data |
| `user_id` | TEXT | User identifier from `engine.run()` |
| `session_id` | TEXT | Session identifier from `engine.run()` |
| `contain_generation` | INTEGER | 1 if this op involves LLM generation, 0 otherwise |
| `metadata` | TEXT | JSON-serialized arbitrary metadata |
| `tags` | TEXT | JSON-serialized array of string tags |
| `status` | TEXT | Always `'flushed'` (reserved for future use) |
| `created_at` | REAL | Unix timestamp (seconds) when the row was inserted |

### Indexes

```sql
CREATE INDEX IF NOT EXISTS idx_request ON traces(request_id);
CREATE INDEX IF NOT EXISTS idx_status ON traces(status, created_at);
CREATE INDEX IF NOT EXISTS idx_created ON traces(created_at);
```

- `idx_request`: Fast lookup for trace detail queries (`WHERE request_id = ?`)
- `idx_status`: Status-based filtering with time ordering
- `idx_created`: Time-based pagination and filtering

## Data Model Types (models.rs)

### Request Types

```rust
struct IngestRequest {
    request_id: String,
    user_id: Option<String>,
    session_id: Option<String>,
    workflow_name: String,
    tags: Option<Vec<String>>,
    graph_structure: Vec<GraphNode>,
    records: Vec<Record>,
}

struct GraphNode {
    op_name: String,
    op_type: Option<String>,
    parent_name: Option<String>,
    contain_generation: Option<bool>,
}

struct Record {
    op_name: String,
    context_id: Option<String>,
    inputs: Option<serde_json::Value>,
    outputs: Option<serde_json::Value>,
    start_time: Option<String>,
    end_time: Option<String>,
    duration_ms: Option<f64>,
    model: Option<String>,
    usage: Option<Usage>,
    cost: Option<f64>,
    metadata: Option<serde_json::Value>,
}

struct Usage {
    prompt_tokens: Option<i64>,
    completion_tokens: Option<i64>,
    total_tokens: Option<i64>,
}
```

### Response Types

```rust
struct IngestResponse { status: String, rows_inserted: usize }
struct TraceListResponse { traces: Vec<TraceSummary>, total: i64, page: i64 }
struct TraceSummary {
    request_id, workflow_name, start_time, total_duration_ms,
    prompt_tokens, completion_tokens, total_tokens, total_cost,
    node_count, tags, input_preview, output_preview
}
struct TraceDetailResponse { request_id: String, nodes: Vec<TraceNode> }
struct TraceNode {
    id, request_id, session_id, op_name, op_type, parent_name,
    context_id, execution_order, start_time, end_time, duration_ms,
    model, prompt_tokens, completion_tokens, total_tokens, cost_usd,
    input, output, contain_generation, metadata, tags,
    children: Vec<TraceNode>  // Recursive tree structure
}
struct DbInfoResponse { path: String, exists: bool, size: u64 }
struct StatusResponse { status: String }
```

## See Also

- [Overview](overview.md) - Architecture, CLI usage, module structure
- [Tracing Overview](../tracing/overview.md) - TraceCollector and FlushWorker internals
