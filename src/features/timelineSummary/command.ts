import { execSync } from 'child_process';
import * as vscode from 'vscode';
import { getEditorContext } from '../../shared/editor';
import { CommitInput } from '../../shared/types';
import { fetchTimelineSummary } from './api';
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

    // 패널이 아직 열려있지 않으면 먼저 focus 명령으로 열어준다
    vscode.commands.executeCommand('codewhy.timelineSummary.focus');
    sidebar.showLoading(ctx);

    await vscode.window.withProgress(
        { location: vscode.ProgressLocation.Notification, title: 'CodeWhy: 타임라인 요약 분석 중...' },
        async () => {
            try {
                const result = await fetchTimelineSummary({
                    filePath: ctx.filePath,
                    repoPath: ctx.repoPath,
                    commits,
                });
                sidebar.setTimeline(ctx, result);
            } catch (err) {
                vscode.window.showErrorMessage(
                    `Timeline Summary 실패: ${(err as Error).message}`
                );
                sidebar.showEmpty();
            }
        },
    );
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
