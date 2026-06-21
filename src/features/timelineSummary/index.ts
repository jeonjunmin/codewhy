import * as vscode from 'vscode';
import { runClearTimelineCache, runTimelineTab } from '../contextBlame/view';

/**
 * Timeline Summary 진입점.
 *
 * 타임라인은 이제 독립 webview 가 아니라 CodeWhy 통합 패널의 '타임라인' 탭으로 표시된다.
 * 명령(codewhy.timelineSummary)은 통합 패널을 열어 해당 탭을 띄우고 분석을 실행한다.
 *
 * 👤 담당: 개발자 B
 */
export function registerTimelineSummary(context: vscode.ExtensionContext) {
    context.subscriptions.push(
        vscode.commands.registerCommand('codewhy.timelineSummary', () => runTimelineTab()),
        vscode.commands.registerCommand('codewhy.timeline.clearCache', () => runClearTimelineCache()),
    );
}
