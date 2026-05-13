import * as vscode from 'vscode';
import { postContextBlame } from '../services/apiClient';

export function registerContextBlame(context: vscode.ExtensionContext) {
    const disposable = vscode.commands.registerCommand('codewhy.contextBlame', async () => {
        const editor = vscode.window.activeTextEditor;
        if (!editor) {
            vscode.window.showWarningMessage('열린 파일이 없습니다.');
            return;
        }

        const filePath = editor.document.uri.fsPath;
        const line = editor.selection.active.line + 1;
        const repoPath = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath ?? '';

        await vscode.window.withProgress(
            { location: vscode.ProgressLocation.Notification, title: 'CodeWhy: 변경 이유 분석 중...' },
            async () => {
                try {
                    const result = await postContextBlame({ filePath, line, repoPath });
                    const panel = vscode.window.createWebviewPanel(
                        'codewhyBlame',
                        `Context Blame — Line ${line}`,
                        vscode.ViewColumn.Beside,
                        {}
                    );
                    panel.webview.html = buildBlameHtml(result, line);
                } catch (err) {
                    vscode.window.showErrorMessage(`분석 실패: ${(err as Error).message}`);
                }
            }
        );
    });

    context.subscriptions.push(disposable);
}

function buildBlameHtml(result: {
    explanation: string;
    commitHash: string;
    author: string;
    date: string;
}, line: number): string {
    return `<!DOCTYPE html>
<html lang="ko">
<body style="font-family: var(--vscode-font-family); padding: 20px;">
  <h2>Context Blame — Line ${line}</h2>
  <p><strong>작성자:</strong> ${result.author} &nbsp;|&nbsp; <strong>날짜:</strong> ${result.date}</p>
  <p><strong>커밋:</strong> <code>${result.commitHash.slice(0, 8)}</code></p>
  <hr/>
  <p>${result.explanation}</p>
</body>
</html>`;
}
