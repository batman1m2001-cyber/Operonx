# Hush Eyes — VS Code Extension

## Overview

Hush Eyes là VS Code extension để hiển thị traces từ Hush workflows. Đọc dữ liệu từ SQLite database (`~/.hush/traces.db`) và render thành tree, timeline, và graph views.

Location: `hush-eyes/src/`

## Kiến trúc Tổng quan

```
Hush Workflow (Python)
    │ writes traces
    ▼
~/.hush/traces.db (SQLite + WAL)
    │ poll file size moi 1s
    ▼
┌─────────────────────────────────────────┐
│           VS Code Extension              │
│                                          │
│  ┌──────────────┐  ┌──────────────────┐  │
│  │ TraceView    │  │   TracePanel     │  │
│  │ Provider     │  │   (Editor)       │  │
│  │ (Sidebar)    │  │                  │  │
│  └──────┬───────┘  └────────┬─────────┘  │
│         │                   │            │
│  ┌──────▼───────────────────▼─────────┐  │
│  │          TraceDatabase             │  │
│  │     (sql.js / WebAssembly)         │  │
│  └────────────────────────────────────┘  │
│         │ postMessage                    │
│  ┌──────▼────────────────────────────┐   │
│  │         WebView (main.js)         │   │
│  │  ┌──────┐ ┌────────┐ ┌───────┐   │   │
│  │  │ List │ │Timeline│ │ Graph │   │   │
│  │  │ View │ │  View  │ │ View  │   │   │
│  │  └──────┘ └────────┘ └───────┘   │   │
│  └───────────────────────────────────┘   │
└──────────────────────────────────────────┘
```

## Entry Point

`extension.ts` register 3 commands và 1 webview provider:

| Command | Mục đích |
|---------|---------|
| `hush.openTraces` | Mở trace viewer trong editor panel |
| `hush.refreshTraces` | Refresh cả sidebar và editor |
| `hush.clearTraces` | Xóa tất cả traces |

```typescript
// Sidebar provider
vscode.window.registerWebviewViewProvider(
    TraceViewProvider.viewType,  // 'hush.traceViewPanel'
    new TraceViewProvider(context.extensionUri)
);
```

## 2 Data Providers

| Provider | Hiển thị | Singleton | Luôn visible |
|----------|---------|-----------|-------------|
| TraceViewProvider | Sidebar panel | `_instance` | Có (khi extension active) |
| TracePanel | Editor panel | `currentPanel` | Không (user mở) |

Cả hai cùng:
- Tạo WebView với HTML từ `webview/index.html`
- Liên kết với `TraceDatabase`
- Poll file changes mỗi 1s
- Xử lý cùng message protocol

## Data Flow

1. **File watcher** detect thay đổi `.db` / `.db-wal` file size
2. **TraceDatabase** checkpoint WAL và đọc SQLite qua sql.js
3. **Provider** gửi message `traceList` / `traceDetail` đến WebView
4. **WebView (main.js)** render UI (list, tree, timeline, graph)
5. **User click** → WebView gửi message ngược lại → Provider query DB

## Kết nối với hush-core

- Cùng đọc `~/.hush/traces.db` được ghi bởi hush-core's BackgroundProcess
- Database schema: bảng `op_traces` và `requests`
- Path resolution: VS Code setting > env var `HUSH_TRACES_DB` > `.env` file > default `~/.hush/traces.db`

## Xem thêm

- [Database Layer](database-layer.md) - sql.js, data models, queries
- [WebView Providers](webview-providers.md) - Provider lifecycle, message protocol
- [Trace Data Model](../tracing/trace-data-model.md) - SQLite schema (hush-core)
- [Async Buffer](../tracing/async-buffer.md) - BackgroundProcess ghi traces
