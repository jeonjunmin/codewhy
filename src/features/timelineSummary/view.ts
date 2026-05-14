import * as vscode from 'vscode';
import { EditorContext } from '../../shared/editor';
import { TimelineResult } from '../../shared/types';

/**
 * Timeline Summary 결과를 사용자에게 표시한다.
 *
 * TODO (개발자 B): 디자인 시안에 맞춰 UI를 구현하세요.
 *  - 추천: Webview 패널에 타임라인 시각화
 *  - 현재는 동작 확인용 임시 InformationMessage 입니다.
 */
export function showTimelineSummaryView(
    context: vscode.ExtensionContext,
    ctx: EditorContext,
    result: TimelineResult
) {
    const fileName = ctx.filePath.split(/[\\/]/).pop() ?? ctx.filePath;
    vscode.window.showInformationMessage(
        `[${fileName}] ${result.summary} · 마일스톤 ${result.milestones.length}건`
    );
}
