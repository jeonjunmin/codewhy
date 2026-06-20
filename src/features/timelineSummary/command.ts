import { execSync } from 'child_process';
import * as vscode from 'vscode';
import { getEditorContext } from '../../shared/editor';
import { CommitInput } from '../../shared/types';
import { streamTimelineSummary } from './api';
import { TimelineSidebarProvider } from './sidebar';

export async function runTimelineSummary(
    _context: vscode.ExtensionContext,
    sidebar: TimelineSidebarProvider,
) {
    const ctx = getEditorContext();
    if (!ctx) { return; }

    const commits = collectGitLog(ctx.repoPath, ctx.filePath);
    if (commits.length === 0) {
        vscode.window.showWarningMessage('CodeWhy: 이 파일의 git 커밋 이력을 찾을 수 없습니다.');
        return;
    }

    vscode.commands.executeCommand('codewhy.timelineSummary.focus');

    // 스트리밍 연결 직전 — "AI가 소스 코드를 분석 중입니다..." 안내 표시,
    // 첫 delta 수신 시 sidebar 가 자동으로 타자기 효과로 전환한다.
    sidebar.startStreaming(ctx);

    try {
        await streamTimelineSummary(
            { filePath: ctx.filePath, repoPath: ctx.repoPath, commits },
            {
                onDelta: (delta) => sidebar.appendStreamDelta(delta),
                onDone:  (result) => sidebar.setTimeline(ctx, result),
                onError: (message) => {
                    vscode.window.showErrorMessage(`Timeline Summary 실패: ${message}`);
                    sidebar.showEmpty();
                },
            },
        );
    } catch (err) {
        vscode.window.showErrorMessage(`Timeline Summary 실패: ${(err as Error).message}`);
        sidebar.showEmpty();
    }
}

function collectGitLog(repoPath: string, filePath: string): CommitInput[] {
    try {
        const out = execSync(
            `git log --follow --format="%H|%an|%ad|%s" --date=short -- "${filePath}"`,
            { cwd: repoPath, timeout: 10_000 }
        ).toString().trim();

        return out.split('\n').filter(Boolean).map(line => {
            const [hash, author, date, ...rest] = line.split('|');
            return { hash, author, date, subject: rest.join('|') };
        });
    } catch {
        return [];
    }
}
