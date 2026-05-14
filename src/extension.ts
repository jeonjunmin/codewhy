import * as vscode from 'vscode';
import { registerContextBlame } from './features/contextBlame';
import { registerTimelineSummary } from './features/timelineSummary';
import { registerRequirementTrace } from './features/requirementTrace';

/**
 * CodeWhy VSCode 확장 진입점.
 *
 * 각 기능은 src/features/<기능명>/ 폴더 안에 캡슐화되어 있고,
 * 여기서는 등록 함수만 차례로 호출한다. 새 기능을 추가할 때도 이 파일만 건드리면 된다.
 */
export function activate(context: vscode.ExtensionContext) {
    registerContextBlame(context);     // 👤 개발자 A
    registerTimelineSummary(context);  // 👤 개발자 B
    registerRequirementTrace(context); // 👤 개발자 C
}

export function deactivate() {}
