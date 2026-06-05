import { execSync } from 'child_process';
import * as vscode from 'vscode';
import { getEditorContext } from '../../shared/editor';
import { CommitInput } from '../../shared/types';
import { fetchTimelineSummary } from './api';
import { showTimelineSummaryView } from './view';

/**
 * `codewhy.timelineSummary` 명령 핸들러.
 *
 * ① 로컬 git log 수집 → ② 서버 전송 → ③ 별도 패널에 타임라인 출력
 *
 * 👤 담당: 개발자 B
 */
export async function runTimelineSummary(context: vscode.ExtensionContext) {
    const ctx = getEditorContext();
    if (!ctx) { return; }

    const commits = collectGitLog(ctx.repoPath, ctx.filePath);
    if (commits.length === 0) {
        vscode.window.showWarningMessage('CodeWhy: 이 파일의 git 커밋 이력을 찾을 수 없습니다.');
        return;
    }

    await vscode.window.withProgress(
        { location: vscode.ProgressLocation.Notification, title: 'CodeWhy: 파일 역사 요약 중...' },
        async () => {
            try {
                const result = await fetchTimelineSummary({
                    filePath: ctx.filePath,
                    repoPath: ctx.repoPath,
                    commits,
                });
                showTimelineSummaryView(ctx, result);
            } catch (err) {
                vscode.window.showErrorMessage(
                    `Timeline Summary 실패: ${(err as Error).message}`
                );
            }
        }
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
