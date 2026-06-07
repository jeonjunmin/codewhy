import * as vscode from 'vscode';
import { runIssueTab } from '../contextBlame/view';

/**
 * Requirement Trace 진입점.
 *
 * 요구사항 역추적은 이제 별도 webview 패널이 아니라 CodeWhy 통합 패널의 '이슈' 탭으로
 * 표시된다. 명령(codewhy.requirementTrace)은 통합 패널을 열어 이슈 탭을 띄우고
 * 현재 라인 기준 역추적을 실행한다.
 *
 * 👤 담당: 개발자 C
 */
export function registerRequirementTrace(context: vscode.ExtensionContext) {
    context.subscriptions.push(
        vscode.commands.registerCommand('codewhy.requirementTrace', () => runIssueTab()),
    );
}
