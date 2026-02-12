import * as vscode from 'vscode';
import { TracePanel } from './tracePanel';
import { TraceViewProvider } from './traceViewProvider';

export function activate(context: vscode.ExtensionContext) {
    console.log('Hush Traces extension activated');

    // Register sidebar webview provider
    const provider = new TraceViewProvider(context.extensionUri);
    context.subscriptions.push(
        vscode.window.registerWebviewViewProvider(
            TraceViewProvider.viewType,
            provider,
            { webviewOptions: { retainContextWhenHidden: true } }
        )
    );

    // Register command: Open Traces (editor panel)
    context.subscriptions.push(
        vscode.commands.registerCommand('hush.openTraces', () => {
            TracePanel.createOrShow(context.extensionUri);
        })
    );

    // Register command: Refresh Traces
    context.subscriptions.push(
        vscode.commands.registerCommand('hush.refreshTraces', () => {
            TracePanel.refresh();
            TraceViewProvider.refresh();
        })
    );

    // Register command: Clear Traces
    context.subscriptions.push(
        vscode.commands.registerCommand('hush.clearTraces', () => {
            TracePanel.clear();
            TraceViewProvider.clear();
        })
    );
}

export function deactivate() {
    console.log('Hush Traces extension deactivated');
}
