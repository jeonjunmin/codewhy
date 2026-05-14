import * as vscode from 'vscode';
import { runRequirementTrace } from './command';

/**
 * Requirement Trace 기능 진입점.
 *
 * 👤 담당: 개발자 C
 */
export function registerRequirementTrace(context: vscode.ExtensionContext) {
    const disposable = vscode.commands.registerCommand(
        'codewhy.requirementTrace',
        () => runRequirementTrace(context)
    );
    context.subscriptions.push(disposable);
}
