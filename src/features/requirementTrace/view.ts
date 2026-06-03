import * as vscode from 'vscode';
import { EditorContext } from '../../shared/editor';
import { TraceResult } from '../../shared/types';

/**
 * Requirement Trace 결과를 사용자에게 표시한다.
 *
 * TODO (개발자 C): 디자인 시안에 맞춰 UI를 구현하세요.
 *  - 추천: QuickPick 으로 문서 선택 → Webview 로 미리보기
 *  - 현재는 동작 확인용 임시 InformationMessage 입니다.
 */
export function showRequirementTraceView(
    context: vscode.ExtensionContext,
    ctx: EditorContext,
    result: TraceResult
) {
    if (result.documents.length === 0) {
        vscode.window.showInformationMessage(`[L${ctx.line}] 연관 기획 문서를 찾지 못했습니다.`);
        return;
    }
    const first = result.documents[0];
    const pageLabel = first.page ? ` (${first.page}p)` : '';
    vscode.window.showInformationMessage(
        `[L${ctx.line}] 연관 문서 ${result.documents.length}건 — 예: ${first.name}${pageLabel}`
    );
}
