import * as vscode from 'vscode';
import { runContextBlame } from './command';
import { registerContextBlameCodeLens } from './view';

/**
 * Context Blame 기능 진입점.
 * extension.ts 에서 호출되어 명령과 CodeLens UI 를 등록한다.
 *
 * 👤 담당: 개발자 A
 */
export function registerContextBlame(context: vscode.ExtensionContext) {
    context.subscriptions.push(
        vscode.commands.registerCommand('codewhy.contextBlame', () => runContextBlame(context)),
    );
    registerContextBlameCodeLens(context);
}
