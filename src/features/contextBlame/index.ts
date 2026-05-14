import * as vscode from 'vscode';
import { runContextBlame } from './command';

/**
 * Context Blame 기능 진입점.
 * extension.ts 에서 호출되어 명령을 등록한다.
 *
 * 👤 담당: 개발자 A
 */
export function registerContextBlame(context: vscode.ExtensionContext) {
    const disposable = vscode.commands.registerCommand(
        'codewhy.contextBlame',
        () => runContextBlame(context)
    );
    context.subscriptions.push(disposable);
}
