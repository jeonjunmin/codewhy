import * as vscode from 'vscode';
import { registerContextBlame } from './features/contextBlame';
import { registerTimelineSummary } from './features/timelineSummary';
import { registerRequirementTrace } from './features/requirementTrace';
import { createHttpClient } from './shared/http';

/**
 * CodeWhy VSCode 확장 진입점.
 *
 * 자동 트리거 시점:
 *   1. 워크스페이스 최초 오픈 (activate 호출)
 *   2. 새 폴더 추가 (onDidChangeWorkspaceFolders)
 *   3. git 커밋 감지 (.git/COMMIT_EDITMSG 파일 변경 감시)
 *   4. 파일 저장 후 30초 디바운스 (커밋 없이 변경이 많을 때 보조 트리거)
 */

const output = vscode.window.createOutputChannel('CodeWhy');

// ── 백엔드 호출 ──────────────────────────────────────────────────────────────

async function initializeProject(projectPath: string): Promise<void> {
    try {
        const res = await createHttpClient().post('/api/v1/project/initialize', {
            project_path: projectPath,
        });

        const status: string = res.data?.status ?? '';

        if (status === 'STARTED') {
            output.appendLine(`[initialize] 분석 시작 — ${projectPath}`);
            vscode.window.setStatusBarMessage('$(loading~spin) CodeWhy: 프로젝트 분석 중…', 3_000);
        } else if (status === 'NO_CHANGES') {
            output.appendLine(`[initialize] 변경 없음, 스킵 — ${projectPath}`);
        } else if (status === 'PROCESSING') {
            output.appendLine(`[initialize] 분석 진행 중 — ${projectPath}`);
        } else if (status === 'ALREADY_COMPLETED') {
            output.appendLine(`[initialize] 이미 완료 — ${projectPath}`);
        }
    } catch {
        output.appendLine(`[initialize] 백엔드 미연결, 스킵 — ${projectPath}`);
    }
}

// ── 디바운스 유틸 ─────────────────────────────────────────────────────────────

function debounce<T extends unknown[]>(
    fn: (...args: T) => void,
    ms: number,
): (...args: T) => void {
    let timer: ReturnType<typeof setTimeout> | undefined;
    return (...args: T) => {
        clearTimeout(timer);
        timer = setTimeout(() => fn(...args), ms);
    };
}

// ── 워크스페이스별 감시자 등록 ──────────────────────────────────────────────

function watchWorkspaceFolder(
    folder: vscode.WorkspaceFolder,
    context: vscode.ExtensionContext,
): void {
    const projectPath = folder.uri.fsPath;

    // ① git 커밋 감지: COMMIT_EDITMSG 는 `git commit` 때마다 갱신된다
    const commitWatcher = vscode.workspace.createFileSystemWatcher(
        new vscode.RelativePattern(folder, '.git/COMMIT_EDITMSG'),
    );
    const onCommit = () => {
        output.appendLine(`[commit] 커밋 감지 → 재분석 트리거 — ${projectPath}`);
        initializeProject(projectPath);
    };
    commitWatcher.onDidChange(onCommit);
    commitWatcher.onDidCreate(onCommit);
    context.subscriptions.push(commitWatcher);

    // ② 파일 저장 디바운스 (30초): 커밋 없이 작업이 많을 때 보조 트리거
    const debouncedSave = debounce((savedPath: string) => {
        output.appendLine(`[save] 파일 저장 디바운스 → 재분석 트리거 — ${savedPath}`);
        initializeProject(projectPath);
    }, 30_000);

    context.subscriptions.push(
        vscode.workspace.onDidSaveTextDocument(doc => {
            // 해당 워크스페이스 폴더 소속 파일만 반응
            if (doc.uri.fsPath.startsWith(projectPath)) {
                debouncedSave(doc.uri.fsPath);
            }
        }),
    );
}

// ── activate ─────────────────────────────────────────────────────────────────

export function activate(context: vscode.ExtensionContext) {
    registerContextBlame(context);     // 👤 개발자 A
    registerTimelineSummary(context);  // 👤 개발자 B
    registerRequirementTrace(context); // 👤 개발자 C

    // 현재 열린 워크스페이스 초기화 + 감시자 등록
    for (const folder of vscode.workspace.workspaceFolders ?? []) {
        initializeProject(folder.uri.fsPath);
        watchWorkspaceFolder(folder, context);
    }

    // 이후 폴더 추가(멀티루트 워크스페이스)도 감지
    context.subscriptions.push(
        vscode.workspace.onDidChangeWorkspaceFolders(e => {
            for (const folder of e.added) {
                initializeProject(folder.uri.fsPath);
                watchWorkspaceFolder(folder, context);
            }
        }),
    );
}

export function deactivate() {}
