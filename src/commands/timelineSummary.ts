import * as vscode from 'vscode';
import { postTimelineSummary } from '../services/apiClient';

export function registerTimelineSummary(context: vscode.ExtensionContext) {
    const disposable = vscode.commands.registerCommand('codewhy.timelineSummary', async () => {
        const editor = vscode.window.activeTextEditor;
        if (!editor) {
            vscode.window.showWarningMessage('열린 파일이 없습니다.');
            return;
        }

        const filePath = editor.document.uri.fsPath;
        const repoPath = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath ?? '';

        await vscode.window.withProgress(
            { location: vscode.ProgressLocation.Notification, title: 'CodeWhy: 파일 역사 요약 중...' },
            async () => {
                try {
                    const result = await postTimelineSummary({ filePath, repoPath });
                    const panel = vscode.window.createWebviewPanel(
                        'codewhyTimeline',
                        `Timeline — ${filePath.split(/[\\/]/).pop()}`,
                        vscode.ViewColumn.Beside,
                        {}
                    );
                    panel.webview.html = buildTimelineHtml(result);
                } catch (err) {
                    vscode.window.showErrorMessage(`요약 실패: ${(err as Error).message}`);
                }
            }
        );
    });

    context.subscriptions.push(disposable);
}

function buildTimelineHtml(result: {
    summary: string;
    milestones: { date: string; description: string }[];
}): string {
    const milestoneRows = result.milestones
        .map(m => `<li><strong>${m.date}</strong> — ${m.description}</li>`)
        .join('');

    return `<!DOCTYPE html>
<html lang="ko">
<body style="font-family: var(--vscode-font-family); padding: 20px;">
  <h2>Timeline Summary</h2>
  <p>${result.summary}</p>
  <hr/>
  <h3>주요 변경 이력</h3>
  <ul>${milestoneRows}</ul>
</body>
</html>`;
}
