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
    const first = result.documents[0];
    vscode.window.showInformationMessage(
        `[L${ctx.line}] 연관 문서 ${result.documents.length}건 — 예: ${first.path} (${first.page}p)`
    );
}
