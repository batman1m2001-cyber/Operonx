import * as vscode from 'vscode';
import * as path from 'path';
import * as fs from 'fs';
import { TraceDatabase } from './database';

export class TracePanel {
    public static currentPanel: TracePanel | undefined;
    public static readonly viewType = 'hushTraces';

    private readonly _panel: vscode.WebviewPanel;
    private readonly _extensionUri: vscode.Uri;
    private _db: TraceDatabase;
    private _disposables: vscode.Disposable[] = [];
    private _pollTimer: NodeJS.Timeout | null = null;
    private _lastDbSize: number = 0;
    private _lastWalSize: number = 0;

    public static createOrShow(extensionUri: vscode.Uri) {
        const column = vscode.window.activeTextEditor
            ? vscode.window.activeTextEditor.viewColumn
            : undefined;

        // If we already have a panel, show it
        if (TracePanel.currentPanel) {
            TracePanel.currentPanel._panel.reveal(column);
            return;
        }

        // Create a new panel
        const panel = vscode.window.createWebviewPanel(
            TracePanel.viewType,
            'Hush Traces',
            column || vscode.ViewColumn.One,
            {
                enableScripts: true,
                retainContextWhenHidden: true,
                localResourceRoots: [
                    vscode.Uri.joinPath(extensionUri, 'webview')
                ]
            }
        );

        panel.iconPath = vscode.Uri.joinPath(extensionUri, 'webview', 'hush-icon-16.png');
        TracePanel.currentPanel = new TracePanel(panel, extensionUri);
    }

    public static refresh() {
        if (TracePanel.currentPanel) {
            TracePanel.currentPanel._refresh();
        }
    }

    public static clear() {
        if (TracePanel.currentPanel) {
            TracePanel.currentPanel._clear();
        }
    }

    private static getDbPath(): string | undefined {
        const config = vscode.workspace.getConfiguration('hush');
        const configPath = config.get<string>('tracesDb');
        return configPath || undefined;
    }

    private constructor(panel: vscode.WebviewPanel, extensionUri: vscode.Uri) {
        this._panel = panel;
        this._extensionUri = extensionUri;
        this._db = new TraceDatabase(TracePanel.getDbPath());

        // Set the webview's initial html content
        this._update();

        // Watch traces.db for changes and auto-refresh
        this._startFileWatcher();

        // Listen for when the panel is disposed
        this._panel.onDidDispose(() => this.dispose(), null, this._disposables);

        // Handle messages from the webview
        this._panel.webview.onDidReceiveMessage(
            message => {
                switch (message.type) {
                    case 'getTraceList':
                        this._sendTraceList(message.timeFilter, message.page);
                        break;
                    case 'getTraceDetail':
                        this._sendTraceDetail(message.requestId);
                        break;
                    case 'getDbInfo':
                        this._sendDbInfo();
                        break;
                    case 'clearTraces':
                        this._clear();
                        break;
                }
            },
            null,
            this._disposables
        );
    }

    private _getFileSize(filePath: string): number {
        try {
            return fs.statSync(filePath).size;
        } catch {
            return 0;
        }
    }

    private _startFileWatcher() {
        this._stopFileWatcher();
        const dbPath = this._db.getDbPath();
        const walPath = dbPath + '-wal';
        this._lastDbSize = this._getFileSize(dbPath);
        this._lastWalSize = this._getFileSize(walPath);

        // Poll every 2 seconds for changes to db or WAL file size
        this._pollTimer = setInterval(() => {
            const dbSize = this._getFileSize(dbPath);
            const walSize = this._getFileSize(walPath);
            if (dbSize !== this._lastDbSize || walSize !== this._lastWalSize) {
                this._lastDbSize = dbSize;
                this._lastWalSize = walSize;
                this._refresh();
            }
        }, 1000);
    }

    private _stopFileWatcher() {
        if (this._pollTimer) {
            clearInterval(this._pollTimer);
            this._pollTimer = null;
        }
    }

    private _refresh() {
        // Recreate database connection to pick up new data
        this._db.close();
        this._db = new TraceDatabase(TracePanel.getDbPath());
        this._sendTraceList();
        this._sendDbInfo();
    }

    private async _clear() {
        try {
            await this._db.clearTraces();
            this._refresh();
            vscode.window.showInformationMessage('Traces cleared');
        } catch (e: any) {
            vscode.window.showErrorMessage(`Failed to clear traces: ${e.message}`);
        }
    }

    private async _sendTraceList(timeFilter?: number, page?: number) {
        try {
            if (!this._db.exists()) {
                this._panel.webview.postMessage({
                    type: 'traceList',
                    traces: [],
                    total: 0,
                    page: 1,
                    error: null
                });
                return;
            }
            const currentPage = page || 1;
            const limit = 50;
            const offset = (currentPage - 1) * limit;
            const result = await this._db.getTraceList({ limit, offset, timeFilter });
            this._panel.webview.postMessage({
                type: 'traceList',
                traces: result.traces,
                total: result.total,
                page: currentPage,
                error: null
            });
        } catch (e: any) {
            this._panel.webview.postMessage({
                type: 'traceList',
                traces: [],
                total: 0,
                page: 1,
                error: e.message
            });
        }
    }

    private async _sendTraceDetail(requestId: string) {
        try {
            const nodes = await this._db.getTraceDetail(requestId);
            this._panel.webview.postMessage({
                type: 'traceDetail',
                requestId,
                nodes,
                error: null
            });
        } catch (e: any) {
            this._panel.webview.postMessage({
                type: 'traceDetail',
                requestId,
                nodes: [],
                error: e.message
            });
        }
    }

    private _sendDbInfo() {
        this._panel.webview.postMessage({
            type: 'dbInfo',
            path: this._db.getDbPath(),
            exists: this._db.exists(),
            size: this._db.getSize()
        });
    }

    public dispose() {
        TracePanel.currentPanel = undefined;
        this._stopFileWatcher();
        this._panel.dispose();
        this._db.close();
        while (this._disposables.length) {
            const x = this._disposables.pop();
            if (x) {
                x.dispose();
            }
        }
    }

    private _update() {
        const webview = this._panel.webview;
        this._panel.title = 'Hush Traces';
        this._panel.webview.html = this._getHtmlForWebview(webview);
    }

    private _getHtmlForWebview(webview: vscode.Webview): string {
        // Get paths to webview resources
        const webviewPath = vscode.Uri.joinPath(this._extensionUri, 'webview');

        // Read the HTML file
        const htmlPath = path.join(webviewPath.fsPath, 'index.html');

        // Check if file exists, if not use inline HTML
        if (fs.existsSync(htmlPath)) {
            let html = fs.readFileSync(htmlPath, 'utf8');
            // Replace resource paths with cache-busting query string
            const cacheBust = Date.now();
            const cssUri = webview.asWebviewUri(vscode.Uri.joinPath(webviewPath, 'styles.css'));
            const jsUri = webview.asWebviewUri(vscode.Uri.joinPath(webviewPath, 'main.js'));
            const hushIcon16Uri = webview.asWebviewUri(vscode.Uri.joinPath(webviewPath, 'hush-icon-16.png'));
            const hushIcon20Uri = webview.asWebviewUri(vscode.Uri.joinPath(webviewPath, 'hush-icon-20.png'));
            const cspSource = webview.cspSource;
            html = html.replace(/\{\{cssUri\}\}/g, `${cssUri.toString()}?v=${cacheBust}`);
            html = html.replace(/\{\{jsUri\}\}/g, `${jsUri.toString()}?v=${cacheBust}`);
            html = html.replace(/\{\{hushIcon16Uri\}\}/g, hushIcon16Uri.toString());
            html = html.replace(/\{\{hushIcon20Uri\}\}/g, hushIcon20Uri.toString());
            html = html.replace(/\{\{cspSource\}\}/g, cspSource);
            return html;
        }

        // Fallback inline HTML
        return this._getInlineHtml();
    }

    private _getInlineHtml(): string {
        return `<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Hush Traces</title>
    <style>
        ${this._getStyles()}
    </style>
</head>
<body>
    <div id="app">
        <div id="header">
            <h1>Hush Traces</h1>
            <div id="actions">
                <button onclick="refresh()">Refresh</button>
                <button onclick="clearTraces()">Clear</button>
            </div>
        </div>
        <div id="db-info"></div>
        <div id="content">
            <div id="trace-list"></div>
            <div id="trace-detail" style="display: none;"></div>
        </div>
    </div>
    <script>
        ${this._getScript()}
    </script>
</body>
</html>`;
    }

    private _getStyles(): string {
        return `
:root {
    --bg-primary: var(--vscode-editor-background);
    --bg-secondary: var(--vscode-sideBar-background);
    --text-primary: var(--vscode-editor-foreground);
    --text-secondary: var(--vscode-descriptionForeground);
    --border-color: var(--vscode-panel-border);
    --accent: var(--vscode-textLink-foreground);
    --hover-bg: var(--vscode-list-hoverBackground);
    --selected-bg: var(--vscode-list-activeSelectionBackground);
}

* {
    box-sizing: border-box;
    margin: 0;
    padding: 0;
}

body {
    font-family: var(--vscode-font-family);
    font-size: var(--vscode-font-size);
    color: var(--text-primary);
    background: var(--bg-primary);
    padding: 16px;
}

#header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 16px;
    padding-bottom: 12px;
    border-bottom: 1px solid var(--border-color);
}

#header h1 {
    font-size: 18px;
    font-weight: 600;
}

#actions button {
    background: var(--vscode-button-background);
    color: var(--vscode-button-foreground);
    border: none;
    padding: 6px 12px;
    margin-left: 8px;
    cursor: pointer;
    border-radius: 3px;
}

#actions button:hover {
    background: var(--vscode-button-hoverBackground);
}

#db-info {
    font-size: 12px;
    color: var(--text-secondary);
    margin-bottom: 16px;
}

/* Trace List */
.trace-table {
    width: 100%;
    border-collapse: collapse;
}

.trace-table th,
.trace-table td {
    padding: 8px 12px;
    text-align: left;
    border-bottom: 1px solid var(--border-color);
}

.trace-table th {
    background: var(--bg-secondary);
    font-weight: 600;
    font-size: 12px;
    text-transform: uppercase;
    color: var(--text-secondary);
}

.trace-table tr:hover {
    background: var(--hover-bg);
    cursor: pointer;
}

.trace-table .duration {
    font-family: monospace;
}

.trace-table .tokens {
    font-family: monospace;
}

.trace-table .cost {
    font-family: monospace;
    color: var(--accent);
}

/* Trace Detail */
#trace-detail {
    display: grid;
    grid-template-columns: 350px 1fr;
    gap: 16px;
    height: calc(100vh - 140px);
}

#detail-header {
    grid-column: 1 / -1;
    display: flex;
    align-items: center;
    gap: 12px;
    padding-bottom: 12px;
    border-bottom: 1px solid var(--border-color);
}

#back-btn {
    background: none;
    border: none;
    color: var(--accent);
    cursor: pointer;
    font-size: 14px;
    padding: 4px 8px;
}

#back-btn:hover {
    text-decoration: underline;
}

#tree-panel {
    overflow: auto;
    background: var(--bg-secondary);
    border-radius: 4px;
    padding: 12px;
}

#node-panel {
    overflow: auto;
    background: var(--bg-secondary);
    border-radius: 4px;
    padding: 16px;
}

/* Tree */
.tree-node {
    padding-left: 20px;
    position: relative;
}

.tree-node::before {
    content: '';
    position: absolute;
    left: 8px;
    top: 0;
    bottom: 0;
    width: 1px;
    background: var(--border-color);
}

.tree-item {
    display: flex;
    align-items: center;
    padding: 4px 8px;
    cursor: pointer;
    border-radius: 3px;
    margin: 2px 0;
}

.tree-item:hover {
    background: var(--hover-bg);
}

.tree-item.selected {
    background: var(--selected-bg);
}

.tree-icon {
    margin-right: 6px;
    font-size: 14px;
}

.tree-name {
    flex: 1;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}

.tree-duration {
    font-size: 11px;
    color: var(--text-secondary);
    margin-left: 8px;
    font-family: monospace;
}

.tree-toggle {
    width: 16px;
    margin-right: 4px;
    cursor: pointer;
    text-align: center;
}

/* Node Detail */
.node-title {
    font-size: 16px;
    font-weight: 600;
    margin-bottom: 16px;
}

.node-metrics {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
    gap: 12px;
    margin-bottom: 20px;
}

.metric {
    background: var(--bg-primary);
    padding: 12px;
    border-radius: 4px;
}

.metric-label {
    font-size: 11px;
    color: var(--text-secondary);
    text-transform: uppercase;
    margin-bottom: 4px;
}

.metric-value {
    font-size: 14px;
    font-family: monospace;
}

.node-section {
    margin-bottom: 16px;
}

.node-section-title {
    font-size: 12px;
    font-weight: 600;
    text-transform: uppercase;
    color: var(--text-secondary);
    margin-bottom: 8px;
}

.json-view {
    background: var(--bg-primary);
    padding: 12px;
    border-radius: 4px;
    font-family: monospace;
    font-size: 12px;
    overflow: auto;
    max-height: 300px;
    white-space: pre-wrap;
    word-break: break-all;
}

.empty-state {
    text-align: center;
    padding: 40px;
    color: var(--text-secondary);
}

.error-state {
    text-align: center;
    padding: 40px;
    color: var(--vscode-errorForeground);
}

.tags-container {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    margin-bottom: 16px;
}

.tag {
    display: inline-block;
    background: var(--vscode-badge-background);
    color: var(--vscode-badge-foreground);
    padding: 3px 8px;
    border-radius: 12px;
    font-size: 11px;
    font-weight: 500;
}

.tag.small {
    padding: 2px 6px;
    font-size: 10px;
}

.tag-more {
    font-size: 10px;
    color: var(--text-secondary);
    margin-left: 4px;
}

.tag-filter-container {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 6px;
    margin-bottom: 12px;
    padding: 8px 12px;
    background: var(--bg-secondary);
    border-radius: 4px;
}

.filter-label {
    font-size: 12px;
    color: var(--text-secondary);
    margin-right: 4px;
}

.tag-filter {
    display: inline-block;
    background: var(--vscode-button-secondaryBackground);
    color: var(--vscode-button-secondaryForeground);
    border: 1px solid var(--border-color);
    padding: 3px 8px;
    border-radius: 12px;
    font-size: 11px;
    cursor: pointer;
}

.tag-filter.selected {
    background: var(--vscode-badge-background);
    color: var(--vscode-badge-foreground);
    border-color: var(--vscode-badge-background);
}

.tag-filter.clear {
    background: transparent;
    border-color: var(--vscode-errorForeground);
    color: var(--vscode-errorForeground);
}

.tags-cell {
    max-width: 200px;
}
`;
    }

    private _getScript(): string {
        return `
const vscode = acquireVsCodeApi();

let currentTraces = [];
let currentNodes = [];
let selectedNode = null;
let currentView = 'list';
let selectedTags = new Set();
let allTags = [];

// Request initial data
vscode.postMessage({ type: 'getTraceList' });
vscode.postMessage({ type: 'getDbInfo' });

// Handle messages from extension
window.addEventListener('message', event => {
    const message = event.data;
    switch (message.type) {
        case 'traceList':
            currentTraces = message.traces;
            if (currentView === 'list') {
                renderTraceList(message.traces, message.error);
            }
            break;
        case 'traceDetail':
            currentNodes = message.nodes;
            renderTraceDetail(message.requestId, message.nodes, message.error);
            break;
        case 'dbInfo':
            renderDbInfo(message);
            break;
    }
});

function refresh() {
    vscode.postMessage({ type: 'getTraceList' });
    vscode.postMessage({ type: 'getDbInfo' });
}

function clearTraces() {
    if (confirm('Clear all traces?')) {
        vscode.postMessage({ type: 'clearTraces' });
        setTimeout(refresh, 100);
    }
}

function renderDbInfo(info) {
    const el = document.getElementById('db-info');
    const sizeKb = (info.size / 1024).toFixed(1);
    el.innerHTML = \`Database: \${info.path} (\${info.exists ? sizeKb + ' KB' : 'not found'})\`;
}

function renderTraceList(traces, error) {
    currentView = 'list';
    const listEl = document.getElementById('trace-list');
    const detailEl = document.getElementById('trace-detail');

    listEl.style.display = 'block';
    detailEl.style.display = 'none';

    if (error) {
        listEl.innerHTML = \`<div class="error-state">Error: \${error}</div>\`;
        return;
    }

    if (!traces || traces.length === 0) {
        listEl.innerHTML = '<div class="empty-state">No traces found. Run a workflow with a tracer to see traces here.</div>';
        return;
    }

    // Collect all unique tags
    allTags = collectAllTags(traces);

    // Filter traces by selected tags
    let filteredTraces = traces;
    if (selectedTags.size > 0) {
        filteredTraces = traces.filter(trace => {
            if (!trace.tags || trace.tags.length === 0) return false;
            return Array.from(selectedTags).every(tag => trace.tags.includes(tag));
        });
    }

    // Render tag filter
    let filterHtml = '';
    if (allTags.length > 0) {
        const tagButtons = allTags.map(tag => {
            const isSelected = selectedTags.has(tag);
            return \`<button class="tag-filter \${isSelected ? 'selected' : ''}" onclick="toggleTagFilter('\${tag}')">\${tag}</button>\`;
        }).join('');
        filterHtml = \`
            <div class="tag-filter-container">
                <span class="filter-label">Filter by tags:</span>
                \${tagButtons}
                \${selectedTags.size > 0 ? '<button class="tag-filter clear" onclick="clearTagFilter()">Clear</button>' : ''}
            </div>
        \`;
    }

    let html = filterHtml + \`
        <table class="trace-table">
            <thead>
                <tr>
                    <th>Workflow</th>
                    <th>Tags</th>
                    <th>Time</th>
                    <th>Duration</th>
                    <th>Tokens</th>
                    <th>Cost</th>
                </tr>
            </thead>
            <tbody>
    \`;

    for (const trace of filteredTraces) {
        const time = trace.start_time ? new Date(trace.start_time).toLocaleString() : '-';
        const duration = trace.total_duration_ms ? (trace.total_duration_ms / 1000).toFixed(2) + 's' : '-';
        const tokens = trace.total_tokens || '-';
        const cost = trace.total_cost ? '$' + trace.total_cost.toFixed(4) : '-';
        const tagsHtml = (trace.tags || []).filter(t => t != null).slice(0, 3).map(t => \`<span class="tag small">\${t}</span>\`).join('') + (trace.tags && trace.tags.length > 3 ? \`<span class="tag-more">+\${trace.tags.length - 3}</span>\` : '');

        html += \`
            <tr onclick="showTrace('\${trace.request_id}')">
                <td>\${trace.workflow_name}</td>
                <td class="tags-cell">\${tagsHtml}</td>
                <td>\${time}</td>
                <td class="duration">\${duration}</td>
                <td class="tokens">\${tokens}</td>
                <td class="cost">\${cost}</td>
            </tr>
        \`;
    }

    html += '</tbody></table>';
    if (filteredTraces.length === 0 && selectedTags.size > 0) {
        html += '<div class="empty-state">No traces match the selected tags.</div>';
    }
    listEl.innerHTML = html;
}

function collectAllTags(traces) {
    const tagSet = new Set();
    for (const trace of traces) {
        if (trace.tags && Array.isArray(trace.tags)) {
            for (const tag of trace.tags) {
                if (tag != null) tagSet.add(tag);
            }
        }
    }
    return Array.from(tagSet).sort();
}

function toggleTagFilter(tag) {
    if (selectedTags.has(tag)) {
        selectedTags.delete(tag);
    } else {
        selectedTags.add(tag);
    }
    renderTraceList(currentTraces, null);
}

function clearTagFilter() {
    selectedTags.clear();
    renderTraceList(currentTraces, null);
}

function showTrace(requestId) {
    vscode.postMessage({ type: 'getTraceDetail', requestId });
}

function renderTraceDetail(requestId, nodes, error) {
    currentView = 'detail';
    const listEl = document.getElementById('trace-list');
    const detailEl = document.getElementById('trace-detail');

    listEl.style.display = 'none';
    detailEl.style.display = 'grid';

    if (error) {
        detailEl.innerHTML = \`<div class="error-state">Error: \${error}</div>\`;
        return;
    }

    const trace = currentTraces.find(t => t.request_id === requestId);
    const workflowName = trace ? trace.workflow_name : 'Unknown';
    const time = trace && trace.start_time ? new Date(trace.start_time).toLocaleString() : '';

    let html = \`
        <div id="detail-header">
            <button id="back-btn" onclick="backToList()">← Back</button>
            <span style="font-weight: 600;">\${workflowName}</span>
            <span style="color: var(--text-secondary);">\${time}</span>
        </div>
        <div id="tree-panel">
            \${renderTree(nodes)}
        </div>
        <div id="node-panel">
            <div class="empty-state">Select a node to view details</div>
        </div>
    \`;

    detailEl.innerHTML = html;

    // Select first node by default
    if (nodes.length > 0) {
        selectNode(nodes[0]);
    }
}

function renderTree(nodes, level = 0) {
    let html = '';
    for (const node of nodes) {
        const hasChildren = node.children && node.children.length > 0;
        const icon = getNodeIcon(node);
        const duration = node.duration_ms ? (node.duration_ms / 1000).toFixed(2) + 's' : '';
        const nodeId = node.id;

        html += \`
            <div class="tree-node" style="padding-left: \${level * 16}px;">
                <div class="tree-item" data-id="\${nodeId}" onclick="selectNodeById(\${nodeId})">
                    <span class="tree-toggle">\${hasChildren ? '▼' : ''}</span>
                    <span class="tree-icon">\${icon}</span>
                    <span class="tree-name">\${getShortName(node.node_name)}</span>
                    <span class="tree-duration">\${duration}</span>
                </div>
                \${hasChildren ? renderTree(node.children, level + 1) : ''}
            </div>
        \`;
    }
    return html;
}

function getNodeIcon(node) {
    if (node.contain_generation) return '⭐';
    if (node.node_name && node.node_name.includes('iteration[')) return '📂';
    if (node.children && node.children.length > 0) return '📦';
    return '○';
}

function getShortName(name) {
    if (!name) return 'unknown';
    const lastDot = name.lastIndexOf('.');
    if (lastDot === -1) return name;
    return name.substring(lastDot + 1);
}

function selectNodeById(id) {
    const node = findNodeById(currentNodes, id);
    if (node) {
        selectNode(node);
    }
}

function findNodeById(nodes, id) {
    for (const node of nodes) {
        if (node.id === id) return node;
        if (node.children) {
            const found = findNodeById(node.children, id);
            if (found) return found;
        }
    }
    return null;
}

function selectNode(node) {
    selectedNode = node;

    // Update selection UI
    document.querySelectorAll('.tree-item').forEach(el => {
        el.classList.remove('selected');
    });
    const el = document.querySelector(\`.tree-item[data-id="\${node.id}"]\`);
    if (el) {
        el.classList.add('selected');
    }

    renderNodeDetail(node);
}

function renderNodeDetail(node) {
    const panel = document.getElementById('node-panel');

    const duration = node.duration_ms ? (node.duration_ms / 1000).toFixed(3) + 's' : '-';
    const model = node.model || '-';
    const tokens = node.total_tokens ? \`\${node.prompt_tokens || 0} → \${node.completion_tokens || 0}\` : '-';
    const cost = node.cost_usd ? '$' + node.cost_usd.toFixed(4) : '-';

    let tagsHtml = '';
    if (node.tags && node.tags.length > 0) {
        const tags = node.tags.filter(t => t != null).map(t => \`<span class="tag">\${t}</span>\`).join('');
        tagsHtml = \`<div class="tags-container">\${tags}</div>\`;
    }

    let html = \`
        <div class="node-title">\${node.node_name}</div>
        \${tagsHtml}
        <div class="node-metrics">
            <div class="metric">
                <div class="metric-label">Duration</div>
                <div class="metric-value">\${duration}</div>
            </div>
            <div class="metric">
                <div class="metric-label">Model</div>
                <div class="metric-value">\${model}</div>
            </div>
            <div class="metric">
                <div class="metric-label">Tokens</div>
                <div class="metric-value">\${tokens}</div>
            </div>
            <div class="metric">
                <div class="metric-label">Cost</div>
                <div class="metric-value">\${cost}</div>
            </div>
        </div>
    \`;

    if (node.input) {
        html += \`
            <div class="node-section">
                <div class="node-section-title">Input</div>
                <div class="json-view">\${JSON.stringify(node.input, null, 2)}</div>
            </div>
        \`;
    }

    if (node.output) {
        html += \`
            <div class="node-section">
                <div class="node-section-title">Output</div>
                <div class="json-view">\${JSON.stringify(node.output, null, 2)}</div>
            </div>
        \`;
    }

    if (node.metadata) {
        html += \`
            <div class="node-section">
                <div class="node-section-title">Metadata</div>
                <div class="json-view">\${JSON.stringify(node.metadata, null, 2)}</div>
            </div>
        \`;
    }

    panel.innerHTML = html;
}

function backToList() {
    currentView = 'list';
    renderTraceList(currentTraces, null);
}
`;
    }
}
