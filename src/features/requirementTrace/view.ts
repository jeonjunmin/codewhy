import * as vscode from 'vscode';
import { EditorContext } from '../../shared/editor';
import { DocumentMatch, TraceMatchType, TraceResult } from '../../shared/types';

/**
 * Requirement Trace 결과를 QuickPick 으로 표시한다.
 *
 * GitHub Issue API 체인으로 연결 확실도가 다른 결과가 섞여 오므로,
 * 각 항목에 신뢰도 배지를 달아 사용자가 확정/추정을 구분하도록 한다.
 * 항목을 고르면 해당 Issue URL 을 외부 브라우저로 연다.
 */

const BADGE: Record<TraceMatchType, string> = {
    issue:    '✓ Issue 연결',
    ticket:   '✓ 티켓 정확',
    semantic: '≈ 추정(검색)',
};

function describe(m: DocumentMatch): string {
    const type = m.matchType ?? 'semantic';
    const badge = BADGE[type];
    const pct = m.confidence != null ? ` · ${Math.round(m.confidence * 100)}%` : '';
    return `${badge}${pct}`;
}

export function showRequirementTraceView(
    context: vscode.ExtensionContext,
    ctx: EditorContext,
    result: TraceResult
) {
    if (result.documents.length === 0) {
        vscode.window.showInformationMessage(`[L${ctx.line}] 연관 GitHub Issue를 찾지 못했습니다.`);
        return;
    }

    const items: (vscode.QuickPickItem & { match: DocumentMatch })[] = result.documents.map((m) => ({
        label: m.title,
        description: describe(m),
        detail: m.excerpt ?? undefined,
        match: m,
    }));

    const top = result.documents[0].matchType ?? 'semantic';
    const placeHolder =
        top === 'issue' || top === 'ticket'
            ? `[L${ctx.line}] 연관 GitHub Issue ${result.documents.length}건`
            : `[L${ctx.line}] 추정 GitHub Issue ${result.documents.length}건 — 직접 연결 Issue 없음`;

    vscode.window.showQuickPick(items, { placeHolder, matchOnDetail: true }).then((picked) => {
        if (picked) {
            vscode.env.openExternal(vscode.Uri.parse(picked.match.url));
        }
    });
}
