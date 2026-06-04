import * as vscode from 'vscode';
import { EditorContext } from '../../shared/editor';
import { DocumentMatch, TraceMatchType, TraceResult } from '../../shared/types';

/**
 * Requirement Trace 결과를 QuickPick 으로 표시한다.
 *
 * 브라운필드(SM) 프로젝트에서는 티켓 정확매칭 외에 백필·시맨틱 '추정' 결과도 섞여 오므로,
 * 각 항목에 신뢰도 배지를 달아 사용자가 확정/추정을 구분하도록 한다. 항목을 고르면
 * 해당 문서 다운로드 URL 을 외부 브라우저로 연다.
 */

const BADGE: Record<TraceMatchType, string> = {
    ticket: '✓ 티켓 정확',
    backfill: '≈ 추정(백필)',
    semantic: '≈ 추정(검색)',
};

function describe(m: DocumentMatch): string {
    const type = m.matchType ?? 'ticket';
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
        vscode.window.showInformationMessage(`[L${ctx.line}] 연관 기획 문서를 찾지 못했습니다.`);
        return;
    }

    const baseURL = vscode.workspace
        .getConfiguration('codewhy')
        .get<string>('backendUrl', 'http://localhost:8000');

    const items: (vscode.QuickPickItem & { match: DocumentMatch })[] = result.documents.map((m) => ({
        label: m.name,
        description: describe(m),
        detail: m.excerpt
            ? `${m.page ? `p.${m.page} · ` : ''}${m.excerpt}`
            : m.page
            ? `p.${m.page}`
            : undefined,
        match: m,
    }));

    const top = result.documents[0].matchType ?? 'ticket';
    const placeHolder =
        top === 'ticket'
            ? `[L${ctx.line}] 연관 기획 문서 ${result.documents.length}건`
            : `[L${ctx.line}] 추정 기획 문서 ${result.documents.length}건 — 정확한 티켓 매칭은 없습니다`;

    vscode.window.showQuickPick(items, { placeHolder, matchOnDetail: true }).then((picked) => {
        if (picked) {
            vscode.env.openExternal(vscode.Uri.parse(`${baseURL}${picked.match.downloadUrl}`));
        }
    });
}
