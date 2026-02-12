# Database Layer

## Overview

Hush Eyes đọc traces từ SQLite database qua sql.js (WebAssembly). Không cần native SQLite bindings — toàn bộ đọc/query chạy trong Node.js process.

Location: `hush-eyes/src/database.ts`

## sql.js (WebAssembly SQLite)

- WebAssembly build của SQLite chạy trong Node.js
- Đọc toàn bộ file `.db` vào memory (`fs.readFileSync`)
- Read-only (không ghi trực tiếp — chỉ clearTraces ghi lại file)
- Không cần build native modules

### WAL Checkpoint

SQLite với WAL mode có thể có writes chưa flush từ `.db-wal`. Trước mỗi lần đọc, extension gọi Python để checkpoint:

```typescript
checkpointWal() {
    // Python one-liner: connect → PRAGMA wal_checkpoint(TRUNCATE) → close
    execSync(`python3 -c "import sqlite3; c=sqlite3.connect('${dbPath}'); c.execute('PRAGMA wal_checkpoint(TRUNCATE)'); c.close()"`);
}
```

## Database Path Resolution

Thứ tự ưu tiên:

1. VS Code setting: `hush.tracesDb` (workspace/user config)
2. Environment variable: `HUSH_TRACES_DB`
3. `.env` file: tìm `HUSH_TRACES_DB=...` trong workspace
4. Default: `~/.hush/traces.db`

```typescript
getDefaultDbPath(): string {
    const config = vscode.workspace.getConfiguration('hush');
    const configPath = config.get<string>('tracesDb');
    if (configPath) return configPath;

    const envPath = process.env.HUSH_TRACES_DB;
    if (envPath) return envPath;

    // ... check .env file ...

    return path.join(os.homedir(), '.hush', 'traces.db');
}
```

## Data Models

### TraceRow

Một dòng trong bảng `op_traces`:

```typescript
interface TraceRow {
    id: number;
    request_id: string;
    workflow_name: string;
    node_name: string | null;
    node_type: string | null;       // "llm", "code", "graph", "for", ...
    parent_name: string | null;
    context_id: string | null;      // "[0]", "[0].[1]" cho loops
    execution_order: number | null;
    start_time: string | null;      // ISO datetime
    end_time: string | null;
    duration_ms: number | null;
    model: string | null;           // LLM model name
    prompt_tokens: number | null;
    completion_tokens: number | null;
    total_tokens: number | null;
    cost_usd: number | null;
    input: string | null;           // JSON string
    output: string | null;          // JSON string
    metadata: string | null;        // JSON string
    tags: string | null;            // JSON array string
    status: string;                 // "flushed", "pending"
    created_at: number;             // Unix timestamp
}
```

### TraceSummary

Tổng hợp cho danh sách traces:

```typescript
interface TraceSummary {
    request_id: string;
    workflow_name: string;
    start_time: string;
    total_duration_ms: number;    // Duration của root node
    prompt_tokens: number;        // Sum từ contain_generation=1 nodes
    completion_tokens: number;
    total_tokens: number;
    total_cost: number;
    node_count: number;
    tags: string[];
    input_preview: string;        // Preview của root input
    output_preview: string;       // Preview của root output
}
```

### TraceNode

Hierarchical node cho trace detail:

```typescript
interface TraceNode {
    id: number;
    request_id: string;
    node_name: string;
    node_type: string | null;
    parent_name: string | null;
    context_id: string | null;
    execution_order: number;
    start_time: string | null;
    end_time: string | null;
    duration_ms: number | null;
    model: string | null;
    prompt_tokens: number | null;
    completion_tokens: number | null;
    total_tokens: number | null;
    cost_usd: number | null;
    input: any;                   // Parsed JSON
    output: any;                  // Parsed JSON
    contain_generation: boolean;
    metadata: any;                // Parsed JSON
    tags: string[] | null;
    children: TraceNode[];        // Built from parent_name
}
```

## Query Patterns

### getTraceList(options)

Paginated list với time filter:

```sql
SELECT DISTINCT request_id, workflow_name, MIN(start_time),
       SUM(CASE WHEN parent_name IS NULL THEN duration_ms END),
       SUM(CASE WHEN contain_generation=1 THEN prompt_tokens END),
       ...
FROM op_traces
WHERE created_at >= ?   -- time filter
GROUP BY request_id
ORDER BY MIN(start_time) DESC
LIMIT 50 OFFSET ?       -- pagination
```

### getTraceDetail(requestId)

Tất cả nodes của một trace:

```sql
SELECT * FROM op_traces
WHERE request_id = ?
ORDER BY execution_order ASC
```

Kết quả được chuyển thành tree qua `buildTree()`.

## Tree Building (buildTree)

Chuyển flat list thành hierarchical tree bằng 3-level matching:

### Level 1: Exact match

```typescript
// Key: "node_name:context_id"
parentMap["workflow.llm:[0]"] = node;
```

### Level 2: Context prefix

Cho nested loops, child context `[0].[1]` tìm parent tại context `[0]`:

```typescript
// Child: op="loop.step", context_id="[0].[1]"
// Parent: op="loop", context_id="[0]"
// Strip last segment: "[0].[1]" → "[0]"
```

### Level 3: Candidate selection

Khi nhiều nodes có cùng parent_name, chọn node phù hợp nhất dựa trên context relationship.

### Ví dụ

```
Input (flat):
  workflow          parent=null     ctx=null
  workflow.prompt   parent=workflow  ctx=null
  workflow.loop     parent=workflow  ctx=null
  workflow.loop.step parent=workflow.loop ctx=[0]
  workflow.loop.step parent=workflow.loop ctx=[1]

Output (tree):
  workflow
  ├── prompt
  └── loop
      ├── step [ctx=0]
      └── step [ctx=1]
```

## Xem thêm

- [Overview](overview.md) - Extension architecture tổng quan
- [WebView Providers](webview-providers.md) - Provider lifecycle
- [Trace Data Model](../tracing/trace-data-model.md) - SQLite schema gốc
