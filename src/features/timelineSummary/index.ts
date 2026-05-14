import * as vscode from 'vscode';
import { runTimelineSummary } from './command';

/**
 * Timeline Summary 기능 진입점.
 *
 * 👤 담당: 개발자 B
 */
export function registerTimelineSummary(context: vscode.ExtensionContext) {
    const disposable = vscode.commands.registerCommand(
        'codewhy.timelineSummary',
        () => runTimelineSummary(context)
    );
    context.subscriptions.push(disposable);
}
