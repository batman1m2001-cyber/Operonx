# Database Schema & Relationships

## Tables

### node_traces

Stores individual node execution traces:

```sql
CREATE TABLE node_traces (
    id INTEGER PRIMARY KEY,
    request_id TEXT NOT NULL,
    workflow_name TEXT NOT NULL,
    node_name TEXT NOT NULL,
    parent_name TEXT,
    context_id TEXT,
    execution_order INTEGER,
    start_time TEXT,
    end_time TEXT,
    duration_ms REAL,
    input_data TEXT,          -- JSON
    output_data TEXT,         -- JSON
    user_id TEXT,
    session_id TEXT,
    model TEXT,
    prompt_tokens INTEGER,
    completion_tokens INTEGER,
    total_tokens INTEGER,
    cost_usd REAL,
    contain_generation INTEGER,
    metadata TEXT,            -- JSON
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_node_traces_request ON node_traces(request_id);
CREATE INDEX idx_node_traces_workflow ON node_traces(workflow_name);
CREATE INDEX idx_node_traces_user ON node_traces(user_id);
CREATE INDEX idx_node_traces_session ON node_traces(session_id);
```

### requests

Tracks request status and tracer config:

```sql
CREATE TABLE requests (
    request_id TEXT PRIMARY KEY,
    status TEXT DEFAULT 'writing',    -- writing, pending, flushing, flushed, failed
    tracer_type TEXT,
    tracer_config TEXT,               -- JSON
    tags TEXT,                        -- JSON array
    error_message TEXT,
    retry_count INTEGER DEFAULT 0,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    completed_at TEXT,
    flushed_at TEXT
);

CREATE INDEX idx_requests_status ON requests(status);
```

## Status Flow

```
writing → pending → flushing → flushed
                 ↓
              failed (retry_count++)
```

1. **writing**: Nodes actively writing traces
2. **pending**: All traces written, ready for flush
3. **flushing**: Background process flushing to external service
4. **flushed**: Successfully sent to external service
5. **failed**: Flush failed (will retry)

## Relationships

```
requests (1) ──────── (*) node_traces
    │
    └── request_id ←── request_id
```

## Query Examples

### Get all traces for a request

```sql
SELECT * FROM node_traces
WHERE request_id = ?
ORDER BY execution_order;
```

### Get pending requests for flush

```sql
SELECT * FROM requests
WHERE status = 'pending'
ORDER BY created_at;
```

### Get LLM usage summary

```sql
SELECT
    workflow_name,
    SUM(prompt_tokens) as total_prompt,
    SUM(completion_tokens) as total_completion,
    SUM(cost_usd) as total_cost
FROM node_traces
WHERE contain_generation = 1
GROUP BY workflow_name;
```

### Filter by tags

```sql
SELECT r.request_id, r.tags
FROM requests r
WHERE json_extract(r.tags, '$') LIKE '%"prod"%';
```
