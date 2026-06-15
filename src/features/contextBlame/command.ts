import * as vscode from 'vscode';
import { getEditorContext } from '../../shared/editor';
import { log } from '../../shared/log';
import { registerContextBlameCodeLens, runBlameTab } from './view';

/**
 * `codewhy.contextBlame` 명령 핸들러.
 *
 * 1. 현재 에디터에서 파일/라인/레포 경로를 얻고
 * 2. 사이드바/보조 UI 초기화를 보장한 뒤
 * 3. 현재 커서 라인을 SSE 스트리밍으로 분석해 사이드바에 점진 렌더한다(view 모듈에 위임).
 *
 * 실제 분석·스트리밍·캐시는 view 모듈(runBlameTab → handleAnalyzeAndShow)이 담당한다.
 *
 * 👤 담당: 개발자 A
 */
export async function runContextBlame(context: vscode.ExtensionContext) {
    log('command', 'runContextBlame 호출됨');
    const ctx = getEditorContext();
    if (!ctx) {
        log('command', 'getEditorContext null — 열린 파일 없음, 중단');
        vscode.window.showInformationMessage('CodeWhy: 분석할 파일을 먼저 열어주세요.');
        return;
    }
    log('command', 'editor ctx', ctx);

    // 사이드바/보조 UI 초기화 보장(중복 호출 무해) 후, 현재 커서 라인을 스트리밍 분석.
    registerContextBlameCodeLens(context);
    runBlameTab();
}
