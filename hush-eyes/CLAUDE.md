# hush-eyes

VS Code extension for visualizing Hush workflow execution traces.

## Project Structure

```
hush-eyes/
├── package.json        # Extension manifest + commands
├── tsconfig.json       # TypeScript config
├── src/
│   ├── extension.ts    # Entry point - activates extension
│   ├── traceViewProvider.ts  # WebviewViewProvider for sidebar
│   ├── tracePanel.ts   # WebView panel logic
│   └── database.ts     # SQLite reader (sql.js)
├── webview/
│   └── main.js         # Frontend JavaScript for WebView
├── resources/
│   └── *.svg           # Icons
└── out/                # Compiled output (gitignored)
```

## How It Works

1. **Trace Database**: Hush workflows write traces to `~/.hush/traces.db` (SQLite)
2. **Extension**: Reads SQLite using sql.js (WebAssembly SQLite)
3. **WebView**: Displays trace data in VS Code sidebar

## Key Files

### extension.ts
- Registers commands: `hush.openTraces`, `hush.refreshTraces`, `hush.clearTraces`
- Registers WebviewViewProvider for sidebar
- Handles activation/deactivation

### traceViewProvider.ts
- Implements `vscode.WebviewViewProvider`
- Creates WebView with trace visualization HTML
- Handles messages from WebView

### database.ts
- Initializes sql.js with WASM
- Reads traces from SQLite database
- Parses and formats trace data

### webview/main.js
- Frontend JavaScript running in WebView
- Renders trace tree/timeline
- Sends commands back to extension

## Commands

| Command | Description |
|---------|-------------|
| `hush.openTraces` | Open trace viewer in sidebar |
| `hush.refreshTraces` | Refresh trace list from database |
| `hush.clearTraces` | Clear all traces from database |

## Configuration

```json
{
  "hush.tracesDb": "/custom/path/to/traces.db"
}
```

Default: `$HUSH_TRACES_DB` env var or `~/.hush/traces.db`

## Build Commands

```bash
# Install dependencies
npm install

# Compile (development)
npm run compile

# Watch mode
npm run watch

# Package for distribution
npm run package
```

## Development

### Adding a New Command

1. Add to `package.json` contributes.commands:
```json
{
  "command": "hush.myCommand",
  "title": "Hush: My Command"
}
```

2. Register in `extension.ts`:
```typescript
context.subscriptions.push(
  vscode.commands.registerCommand('hush.myCommand', () => {
    // Implementation
  })
);
```

### Modifying WebView

1. Update HTML generation in `traceViewProvider.ts`
2. Update frontend logic in `webview/main.js`
3. Use `webview.postMessage()` for extension → WebView
4. Use `acquireVsCodeApi().postMessage()` for WebView → extension

## Data Flow

```
Python Workflow
    ↓ (writes)
~/.hush/traces.db (SQLite)
    ↓ (reads via sql.js)
database.ts
    ↓ (passes data)
traceViewProvider.ts
    ↓ (renders)
WebView (HTML/JS)
```

## Testing

Currently manual testing:
1. `npm run compile`
2. Press F5 in VS Code to launch Extension Development Host
3. Run a Hush workflow to generate traces
4. Open Hush Traces from Activity Bar

## Dependencies

- **sql.js**: WebAssembly SQLite for reading trace database
- **better-sqlite3**: Native SQLite (dev dependency for type definitions)
- **esbuild**: Fast bundler for extension code
