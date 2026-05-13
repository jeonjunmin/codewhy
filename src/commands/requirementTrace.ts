import * as vscode from 'vscode';
import { postRequirementTrace } from '../services/apiClient';

export function registerRequirementTrace(context: vscode.ExtensionContext) {
    const disposable = vscode.commands.registerCommand('codewhy.requirementTrace', async () => {
        const editor = vscode.window.activeTextEditor;
        if (!editor) {
            vscode.window.showWarningMessage('열린 파일이 없습니다.');
            return;
        }

        const config = vscode.workspace.getConfiguration('codewhy');
        const documentPaths = config.get<string[]>('documentPaths', []);
        if (documentPaths.length === 0) {
            vscode.window.showWarningMessage(
                'CodeWhy: 기획서 폴더가 설정되지 않았습니다. 설정에서 codewhy.documentPaths를 추가하세요.'
            );
            return;
        }

        const filePath = editor.document.uri.fsPath;
        const line = editor.selection.active.line + 1;
        const repoPath = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath ?? '';

        await vscode.window.withProgress(
            { location: vscode.ProgressLocation.Notification, title: 'CodeWhy: 연관 기획서 검색 중...' },
            async () => {
                try {
                    const result = await postRequirementTrace({ filePath, line, repoPath });
                    if (result.documents.length === 0) {
                        vscode.window.showInformationMessage('연관된 기획 문서를 찾지 못했습니다.');
                        return;
                    }
                    const panel = vscode.window.createWebviewPanel(
                        'codewhyTrace',
                        `Requirement Trace — Line ${line}`,
                        vscode.ViewColumn.Beside,
                        {}
                    );
                    panel.webview.html = buildTraceHtml(result.documents, line);
                } catch (err) {
                    vscode.window.showErrorMessage(`검색 실패: ${(err as Error).message}`);
                }
            }
        );
    });

    context.subscriptions.push(disposable);
}

function buildTraceHtml(
    documents: { path: string; page: number; excerpt: string }[],
    line: number
): string {
    const docRows = documents
        .map(
            d => `<div style="margin-bottom:16px; border-left:3px solid #0078d4; padding-left:12px;">
        <p><strong>${d.path}</strong> — ${d.page}페이지</p>
        <blockquote>${d.excerpt}</blockquote>
      </div>`
        )
        .join('');

    return `<!DOCTYPE html>
<html lang="ko">
<body style="font-family: var(--vscode-font-family); padding: 20px;">
  <h2>Requirement Trace — Line ${line}</h2>
  <p>연관된 기획 문서 ${documents.length}건</p>
  <hr/>
  ${docRows}
</body>
</html>`;
}
