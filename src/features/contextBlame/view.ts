import * as vscode from 'vscode';
import { EditorContext } from '../../shared/editor';
import { BlameResult } from '../../shared/types';

/**
 * Context Blame 결과를 사용자에게 표시한다.
 *
 * TODO (개발자 A): 디자인 시안에 맞춰 UI를 구현하세요.
 *  - Webview 패널 / 인라인 데코레이션 / 사이드바 뷰 / Hover 등 자유 선택
 *  - 현재는 동작 확인용 임시 InformationMessage 입니다.
 */
export function showContextBlameView(
    context: vscode.ExtensionContext,
    ctx: EditorContext,
    result: BlameResult
) {
    vscode.window.showInformationMessage(
        `[L${ctx.line}] ${result.author} · ${result.date}\n${result.explanation}`
    );
}
