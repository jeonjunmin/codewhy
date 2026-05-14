import * as vscode from 'vscode';

/**
 * 현재 에디터에서 백엔드 호출에 필요한 컨텍스트(파일/라인/레포 경로)를 뽑아낸다.
 * 열린 파일이 없으면 경고를 띄우고 null 을 반환한다.
 *
 * 세 기능 모두 동일한 컨텍스트 수집 로직을 쓰기 때문에 여기에 모았다.
 */
export interface EditorContext {
    filePath: string;
    line: number;
    repoPath: string;
}

export function getEditorContext(): EditorContext | null {
    const editor = vscode.window.activeTextEditor;
    if (!editor) {
        vscode.window.showWarningMessage('CodeWhy: 열린 파일이 없습니다.');
        return null;
    }
    return {
        filePath: editor.document.uri.fsPath,
        line: editor.selection.active.line + 1,
        repoPath: vscode.workspace.workspaceFolders?.[0]?.uri.fsPath ?? '',
    };
}
