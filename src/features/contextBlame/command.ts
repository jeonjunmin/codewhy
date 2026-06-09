import * as vscode from 'vscode';
import { getEditorContext } from '../../shared/editor';
import { log } from '../../shared/log';
import { fetchContextBlame } from './api';
import { showContextBlameView } from './view';

/**
 * `codewhy.contextBlame` 명령 핸들러.
 *
 * 1. 현재 에디터에서 파일/라인/레포 경로를 얻고
 * 2. 백엔드에 분석을 요청하고
 * 3. 결과를 view 모듈에 전달한다.
 *
 * 👤 담당: 개발자 A
 */
export async function runContextBlame(context: vscode.ExtensionContext) {
    log('command', 'runContextBlame 호출됨');
    const ctx = getEditorContext();
    if (!ctx) {
        log('command', 'getEditorContext null — 열린 파일 없음, 중단');
        return;
    }
    log('command', 'editor ctx', ctx);

    await vscode.window.withProgress(
        {
            location: vscode.ProgressLocation.Notification,
            title: 'CodeWhy: 변경 이유 분석 중...',
        },
        async () => {
            try {
                const result = await fetchContextBlame(ctx);
                log('command', 'fetch 성공 — showContextBlameView 호출', { explanationLen: result.explanation?.length });
                showContextBlameView(context, ctx, result);
            } catch (err) {
                log('command', 'fetch 실패', (err as Error).message);
                vscode.window.showErrorMessage(
                    `Context Blame 실패: ${(err as Error).message}`
                );
            }
        }
    );
}
