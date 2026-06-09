import * as vscode from 'vscode';

/**
 * CodeWhy 공용 출력 채널 로거.
 *
 * 화면에서 바로 디버깅할 수 있도록 모든 기능이 같은 "CodeWhy" 출력 채널에 쓴다.
 * (View → Output → CodeWhy 에서 확인)
 */
export const channel = vscode.window.createOutputChannel('CodeWhy');

/** CodeWhy 출력 채널을 화면에 띄운다(포커스는 에디터에 유지). */
export function showLog(): void {
    channel.show(true);
}

export function log(scope: string, message: string, data?: unknown): void {
    const ts = new Date().toISOString().slice(11, 23);
    const extra = data === undefined ? '' : ` ${safeStringify(data)}`;
    channel.appendLine(`${ts} [${scope}] ${message}${extra}`);
}

function safeStringify(v: unknown): string {
    try {
        return typeof v === 'string' ? v : JSON.stringify(v);
    } catch {
        return String(v);
    }
}
