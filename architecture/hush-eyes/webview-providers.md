# WebView Providers

## Overview

Hush Eyes có 2 webview providers cùng chia sẻ cùng logic nhưng hiển thị ở vị trí khác nhau trong VS Code.

Location: `hush-eyes/src/traceViewProvider.ts`, `tracePanel.ts`, `webview/main.js`

## 2 Providers

| | TraceViewProvider | TracePanel |
|---|---|---|
| **Vị trí** | Sidebar (Activity Bar) | Editor area |
| **VS Code API** | `WebviewViewProvider` | `WebviewPanel` |
| **Luôn visible** | Có (khi extension active) | Không (user phải mở) |
| **Singleton** | `_instance` | `currentPanel` |
| **View ID** | `hush.traceViewPanel` | `hushTraces` |

## Lifecycle

### TraceViewProvider

```
1. Extension activate
   └── registerWebviewViewProvider('hush.traceViewPanel', provider)

2. VS Code hiển thị sidebar
   └── resolveWebviewView(webviewView)
       ├── Set webview options (enableScripts, localResourceRoots)
       ├── Load HTML từ webview/index.html
       ├── Attach message handler (onDidReceiveMessage)
       └── Start file watcher (polling 1s)

3. Webview request data
   └── postMessage({type: 'getTraceList'})
       └── Provider query DB → postMessage({type: 'traceList', data})

4. File change detected
   └── Recreate DB, refresh webview

5. Extension deactivate
   └── Stop file watcher, close DB
```

### TracePanel

```
1. User chạy command 'hush.openTraces'
   └── TracePanel.createOrShow(extensionUri)
       ├── Nếu currentPanel tồn tại → panel.reveal()
       └── Nếu không → createWebviewPanel() + setup

2-5: Tương tự TraceViewProvider
```

## File Watcher

Sử dụng **polling** (không phải `fs.watch`) vì ổn định hơn trên mọi OS:

```typescript
setInterval(() => {
    const dbSize = getFileSize(dbPath);
    const walSize = getFileSize(dbPath + '-wal');

    if (dbSize !== lastDbSize || walSize !== lastWalSize) {
        lastDbSize = dbSize;
        lastWalSize = walSize;
        // Recreate DB connection và reload UI
        db.close();
        db = new TraceDatabase(dbPath);
        sendTraceList();
    }
}, 1000);  // Mỗi 1 giây
```

Tại sao monitor cả `.db` và `.db-wal`?
- SQLite WAL mode ghi vào `.db-wal` trước
- Chỉ sau khi checkpoint thì data mới vào `.db`
- Monitor cả hai để detect writes sớm nhất

## Message Protocol

### WebView → Extension

```typescript
// Lấy danh sách traces (paginated)
{ type: 'getTraceList', timeFilter?: number, page?: number }

// Lấy chi tiết một trace
{ type: 'getTraceDetail', requestId: string }

// Lấy thông tin database
{ type: 'getDbInfo' }

// Xóa tất cả traces
{ type: 'clearTraces' }
```

### Extension → WebView

```typescript
// Danh sách traces
{ type: 'traceList', traces: TraceSummary[], total: number, page: number, error?: string }

// Chi tiết trace (hierarchical tree)
{ type: 'traceDetail', requestId: string, nodes: TraceNode[], error?: string }

// Thông tin database
{ type: 'dbInfo', path: string, exists: boolean, size: number }
```

## Frontend (main.js)

### 3 Visualization Modes

#### 1. List View

Danh sách tất cả traces với filters:

- **Time filter**: 1h, 24h, 7d, 30d, All
- **Search**: Workflow name, tags, input/output preview
- **Tag filter**: Multi-select buttons
- **Pagination**: 50 traces/page
- **Columns**: Workflow, Input, Output, Tags, Latency, Tokens, Cost

#### 2. Tree View

Nested hierarchical tree của các ops trong một trace:

```
🏠 workflow (1.2s)
├── ✎ prompt (5ms)
├── 🧠 llm (1.1s)
│   └── model: gpt-4o, tokens: 150→50
└── ƒ process (85ms)
```

- Expand/collapse nodes
- Click để xem chi tiết
- Icons theo node type

#### 3. Timeline View

Horizontal bars hiển thị execution timing:

```
|--prompt--|
|----------llm--------------------------|
                    |--process--|
├─────┼─────┼─────┼─────┼─────┼─────┤
0    200   400   600   800  1000  1200ms
```

#### 4. Graph View

DAG layout (left-to-right):

```
[prompt] ──→ [llm] ──→ [process]
```

- Bezier curve edges
- Color-coded by node type
- 180x48px nodes

### Node Detail Panel

Khi click một node, hiển thị:

- **Header**: Node name, type badge, trace ID
- **Metrics**: Duration, Model, Tokens (prompt→completion), Cost
- **Preview tab**: Key-value tables cho input/output/metadata
- **Log tab**: Raw JSON với syntax highlighting
- **Connects**: Hiển thị input/output connections đến các nodes khác

### Node Type Icons

| Type | Icon | Màu |
|------|------|-----|
| llm | 🧠 | Purple |
| embedding | ◈ | Purple |
| rerank | ⇅ | Purple |
| branch | ⑂ | Orange |
| for / map | ↻ / ⊶ | Orange |
| while | ↺ | Yellow |
| code / lambda | ƒ / λ | Green |
| prompt | ✎ | Pink |
| graph | 🏠 | Cyan |
| iteration | ⟳ | Cyan |

## Cache Busting

Resource URIs được thêm timestamp để tránh cached assets:

```typescript
const jsUri = webview.asWebviewUri(jsPath) + `?v=${Date.now()}`;
```

## Content Security Policy

WebView sử dụng CSP để bảo mật:

```html
<meta http-equiv="Content-Security-Policy"
    content="default-src 'none';
    style-src ${cspSource} 'unsafe-inline' https://fonts.googleapis.com;
    font-src https://fonts.gstatic.com;
    script-src ${cspSource};
    img-src ${cspSource} data:;">
```

## Xem thêm

- [Overview](overview.md) - Extension architecture tổng quan
- [Database Layer](database-layer.md) - sql.js, data models, queries
