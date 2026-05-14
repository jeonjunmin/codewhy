import * as vscode from 'vscode';
import { getEditorContext } from '../../shared/editor';
import { fetchTimelineSummary } from './api';
import { showTimelineSummaryView } from './view';

/**
 * `codewhy.timelineSummary` 명령 핸들러.
 *
 * 1. 현재 파일/레포 경로를 얻고
 * 2. 백엔드에 요약을 요청하고
 * 3. 결과를 view 모듈에 전달한다.
 *
 * 👤 담당: 개발자 B
 */
export async function runTimelineSummary(context: vscode.ExtensionContext) {
    const ctx = getEditorContext();
    if (!ctx) {
        return;
    }

    await vscode.window.withProgress(
        {
            location: vscode.ProgressLocation.Notification,
            title: 'CodeWhy: 파일 역사 요약 중...',
        },
        async () => {
            try {
                const result = await fetchTimelineSummary({
                    filePath: ctx.filePath,
                    repoPath: ctx.repoPath,
                });
                showTimelineSummaryView(context, ctx, result);
            } catch (err) {
                vscode.window.showErrorMessage(
                    `Timeline Summary 실패: ${(err as Error).message}`
                );
            }
        }
    );
}
