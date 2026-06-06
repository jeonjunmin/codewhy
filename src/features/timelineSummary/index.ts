import * as vscode from 'vscode';
import { runTimelineSummary } from './command';
import { TimelineSidebarProvider, VIEW_ID } from './sidebar';

export function registerTimelineSummary(context: vscode.ExtensionContext) {
    const sidebar = new TimelineSidebarProvider(context.extensionUri);

    context.subscriptions.push(
        vscode.window.registerWebviewViewProvider(VIEW_ID, sidebar, {
            webviewOptions: { retainContextWhenHidden: true },
        }),
        vscode.commands.registerCommand(
            'codewhy.timelineSummary',
            () => runTimelineSummary(context, sidebar),
        ),
    );
}
