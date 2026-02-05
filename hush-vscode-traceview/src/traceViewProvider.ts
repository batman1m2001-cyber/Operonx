import * as vscode from 'vscode';
import * as path from 'path';
import * as fs from 'fs';
import { TraceDatabase } from './database';

export class TraceViewProvider implements vscode.WebviewViewProvider {
    public static readonly viewType = 'hush.traceViewPanel';
    private static _instance: TraceViewProvider | undefined;

    private _view?: vscode.WebviewView;
    private _extensionUri: vscode.Uri;
    private _db: TraceDatabase;
    private _pollTimer: NodeJS.Timeout | null = null;
    private _lastDbSize: number = 0;
    private _lastWalSize: number = 0;

    constructor(extensionUri: vscode.Uri) {
        this._extensionUri = extensionUri;
        this._db = new TraceDatabase(TraceViewProvider._getDbPath());
        TraceViewProvider._instance = this;
    }

    public static refresh() {
        if (TraceViewProvider._instance) {
            TraceViewProvider._instance._refresh();
        }
    }

    public static clear() {
        if (TraceViewProvider._instance) {
            TraceViewProvider._instance._clear();
        }
    }

    private static _getDbPath(): string | undefined {
        const config = vscode.workspace.getConfiguration('hush');
        const configPath = config.get<string>('tracesDb');
        return configPath || undefined;
    }

    public resolveWebviewView(
        webviewView: vscode.WebviewView,
        _context: vscode.WebviewViewResolveContext,
        _token: vscode.CancellationToken
    ) {
        this._view = webviewView;

        webviewView.webview.options = {
            enableScripts: true,
            localResourceRoots: [
                vscode.Uri.joinPath(this._extensionUri, 'webview')
            ]
        };

        webviewView.webview.html = this._getHtmlForWebview(webviewView.webview);

        this._startFileWatcher();

        webviewView.onDidDispose(() => {
            this._stopFileWatcher();
            this._db.close();
        });

        webviewView.webview.onDidReceiveMessage(message => {
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
        });
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
        this._db.close();
        this._db = new TraceDatabase(TraceViewProvider._getDbPath());
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
        if (!this._view) return;
        try {
            if (!this._db.exists()) {
                this._view.webview.postMessage({
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
            this._view.webview.postMessage({
                type: 'traceList',
                traces: result.traces,
                total: result.total,
                page: currentPage,
                error: null
            });
        } catch (e: any) {
            this._view.webview.postMessage({
                type: 'traceList',
                traces: [],
                total: 0,
                page: 1,
                error: e.message
            });
        }
    }

    private async _sendTraceDetail(requestId: string) {
        if (!this._view) return;
        try {
            const nodes = await this._db.getTraceDetail(requestId);
            this._view.webview.postMessage({
                type: 'traceDetail',
                requestId,
                nodes,
                error: null
            });
        } catch (e: any) {
            this._view.webview.postMessage({
                type: 'traceDetail',
                requestId,
                nodes: [],
                error: e.message
            });
        }
    }

    private _sendDbInfo() {
        if (!this._view) return;
        this._view.webview.postMessage({
            type: 'dbInfo',
            path: this._db.getDbPath(),
            exists: this._db.exists(),
            size: this._db.getSize()
        });
    }

    private _getHtmlForWebview(webview: vscode.Webview): string {
        const webviewPath = vscode.Uri.joinPath(this._extensionUri, 'webview');
        const htmlPath = path.join(webviewPath.fsPath, 'index.html');

        if (fs.existsSync(htmlPath)) {
            let html = fs.readFileSync(htmlPath, 'utf8');
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

        return `<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><title>Hush Traces</title></head>
<body><p>Error: webview files not found.</p></body>
</html>`;
    }
}
