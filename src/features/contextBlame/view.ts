import * as vscode from 'vscode';
import { EditorContext } from '../../shared/editor';
import { BlameResult } from '../../shared/types';
import { fetchContextBlame } from './api';

/**
 * Context Blame UI 레이어.
 *
 * 동작 흐름:
 *  1. 확장 활성화 → registerContextBlameCodeLens()
 *  2. 커서가 있는 라인에 🔍 CodeLens 표시
 *  3. 렌즈 클릭 → 백엔드 분석(캐시 있으면 스킵)
 *  4. 해당 라인으로 커서 이동 + editor.action.showHover 실행
 *  5. HoverProvider가 캐시에서 결과를 꺼내 에디터 위 팝업으로 렌더링
 *
 * 호버는 CodeLens 클릭 때만 트리거 — 마우스-오버만으로는 분석 전 팝업이 뜨지 않는다.
 *
 * 👤 담당: 개발자 A
 */

// ─── 타입 ─────────────────────────────────────────────────────────────────────
interface CacheEntry { ctx: EditorContext; result: BlameResult; }

// ─── 전역 상태 ────────────────────────────────────────────────────────────────
const blameCache = new Map<string, CacheEntry>();
const pinned = new Set<string>();

let statusBar: vscode.StatusBarItem | undefined;
let pinDecoration: vscode.TextEditorDecorationType | undefined;
let codeLensEmitter: vscode.EventEmitter<void> | undefined;
let cardPanel: vscode.WebviewPanel | undefined;
let currentFilePath = '';
let currentCursorLine = -1;
let initialized = false;

const cacheKey = (filePath: string, line: number) => `${filePath}:${line}`;

// ─────────────────────────────────────────────────────────────────────────────
// 공개 API
// ─────────────────────────────────────────────────────────────────────────────

export function registerContextBlameCodeLens(context: vscode.ExtensionContext) {
    ensureInitialized(context);
}

export function showContextBlameView(
    context: vscode.ExtensionContext,
    ctx: EditorContext,
    result: BlameResult,
) {
    ensureInitialized(context);
    blameCache.set(cacheKey(ctx.filePath, ctx.line), { ctx, result });
    updateStatusBar(ctx.line, result);
    openCardPanel(ctx, result);
}

// ─────────────────────────────────────────────────────────────────────────────
// 1. 초기화
// ─────────────────────────────────────────────────────────────────────────────
function ensureInitialized(context: vscode.ExtensionContext) {
    if (initialized) { return; }
    initialized = true;

    statusBar = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 100);
    context.subscriptions.push(statusBar);

    pinDecoration = vscode.window.createTextEditorDecorationType({
        after: {
            margin: '0 0 0 2em',
            color: new vscode.ThemeColor('editorCodeLens.foreground'),
            fontStyle: 'italic',
        },
    });
    context.subscriptions.push(pinDecoration);

    // HoverProvider — 캐시에 분석 결과가 있는 라인만 팝업 표시
    context.subscriptions.push(
        vscode.languages.registerHoverProvider({ scheme: 'file' }, {
            provideHover(document, position) {
                const key = cacheKey(document.uri.fsPath, position.line + 1);
                const entry = blameCache.get(key);
                if (!entry) { return null; }
                return new vscode.Hover(
                    buildHoverMarkdown(entry.ctx, entry.result),
                    document.lineAt(position.line).range,
                );
            },
        }),
    );

    // CodeLens — 커서가 있는 라인에만 🔍 렌즈 표시
    codeLensEmitter = new vscode.EventEmitter<void>();
    context.subscriptions.push(codeLensEmitter);
    context.subscriptions.push(
        vscode.languages.registerCodeLensProvider(
            { scheme: 'file' },
            {
                onDidChangeCodeLenses: codeLensEmitter.event,
                provideCodeLenses(document) {
                    if (
                        document.uri.fsPath !== currentFilePath ||
                        currentCursorLine < 0 ||
                        currentCursorLine >= document.lineCount
                    ) { return []; }
                    if (document.lineAt(currentCursorLine).isEmptyOrWhitespace) { return []; }
                    const repoPath = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath ?? '';
                    const range = new vscode.Range(currentCursorLine, 0, currentCursorLine, 0);
                    return [
                        new vscode.CodeLens(range, {
                            title: '🔍 왜 바꿨어?',
                            command: 'codewhy.blame.analyzeAndShow',
                            arguments: [{ filePath: document.uri.fsPath, line: currentCursorLine + 1, repoPath }],
                        }),
                    ];
                },
            },
        ),
    );

    // 커서 이동 감지 → CodeLens 위치 갱신
    context.subscriptions.push(
        vscode.window.onDidChangeTextEditorSelection(e => {
            const newLine = e.selections[0].active.line;
            const newPath = e.textEditor.document.uri.fsPath;
            if (newLine !== currentCursorLine || newPath !== currentFilePath) {
                currentCursorLine = newLine;
                currentFilePath = newPath;
                codeLensEmitter!.fire();
            }
        }),
        vscode.window.onDidChangeActiveTextEditor(editor => {
            if (editor) { refreshPinnedDecorations(editor); }
        }),
    );

    // 보조 명령 등록
    context.subscriptions.push(
        vscode.commands.registerCommand('codewhy.blame.analyzeAndShow', handleAnalyzeAndShow),
        vscode.commands.registerCommand('codewhy.blame.openCommit', openCommitInTerminal),
        vscode.commands.registerCommand('codewhy.blame.pin', togglePin),
    );
}

// ─────────────────────────────────────────────────────────────────────────────
// 2. CodeLens 클릭 → 분석 → 에디터 위 팝업
// ─────────────────────────────────────────────────────────────────────────────
async function handleAnalyzeAndShow(args: { filePath: string; line: number; repoPath: string }) {
    const key = cacheKey(args.filePath, args.line);
    let entry = blameCache.get(key);

    if (!entry) {
        await vscode.window.withProgress(
            { location: vscode.ProgressLocation.Notification, title: 'CodeWhy: 변경 이유 분석 중...' },
            async () => {
                try {
                    const result = await fetchContextBlame(args);
                    entry = { ctx: args, result };
                    blameCache.set(key, entry);
                } catch (err) {
                    vscode.window.showErrorMessage(`Context Blame 실패: ${(err as Error).message}`);
                }
            },
        );
    }

    if (!entry) { return; }
    updateStatusBar(args.line, entry.result);
    openCardPanel(entry.ctx, entry.result);
}

// ─────────────────────────────────────────────────────────────────────────────
// 3. WebView 카드 패널 — "이름표 대신 사유서" 시안 풀-디자인 렌더
// ─────────────────────────────────────────────────────────────────────────────
function openCardPanel(ctx: EditorContext, r: BlameResult) {
    if (!cardPanel) {
        cardPanel = vscode.window.createWebviewPanel(
            'codewhy.contextBlameCard',
            'AI Cop',
            { viewColumn: vscode.ViewColumn.Beside, preserveFocus: true },
            { enableScripts: true, retainContextWhenHidden: true },
        );
        cardPanel.onDidDispose(() => { cardPanel = undefined; });
        cardPanel.webview.onDidReceiveMessage(msg => handleCardMessage(msg, ctx, r));
    }
    cardPanel.title = `AI Cop · L${ctx.line}`;
    cardPanel.webview.html = renderCardHtml(ctx, r);
    cardPanel.reveal(vscode.ViewColumn.Beside, true);
}

function handleCardMessage(
    msg: { type: string; payload?: unknown },
    ctx: EditorContext,
    r: BlameResult,
) {
    switch (msg.type) {
        case 'openSpec':
            vscode.commands.executeCommand('codewhy.requirementTrace');
            break;
        case 'openCommit':
            vscode.commands.executeCommand('codewhy.blame.openCommit', {
                commitHash: r.commitHash, repoPath: ctx.repoPath,
            });
            break;
        case 'openHistory':
            vscode.commands.executeCommand('codewhy.timelineSummary');
            break;
        case 'togglePin':
            vscode.commands.executeCommand('codewhy.blame.pin', {
                filePath: ctx.filePath, line: ctx.line,
            });
            break;
        case 'close':
            cardPanel?.dispose();
            break;
        case 'copy':
            vscode.env.clipboard.writeText(formatPlain(ctx, r));
            vscode.window.setStatusBarMessage('CodeWhy: 카드 내용을 클립보드에 복사했어요.', 2000);
            break;
    }
}

function formatPlain(ctx: EditorContext, r: BlameResult): string {
    const file = ctx.filePath.split(/[\\/]/).pop() ?? ctx.filePath;
    return [
        `[CodeWhy] ${file} : L${ctx.line}`,
        formatNarrative(r).replace(/<[^>]+>/g, ''),
        r.commitHash ? `commit: ${r.commitHash.slice(0, 7)}` : '',
        r.ticket ? `ticket: ${r.ticket}` : '',
        r.specRef ? `spec: ${r.specRef}` : '',
        r.team ? `team: ${r.team}` : '',
        r.aiSuggestion ? `AI 추론: ${r.aiSuggestion}` : '',
    ].filter(Boolean).join('\n');
}

// ─────────────────────────────────────────────────────────────────────────────
// 4. 호버 마크다운 빌더 — "이름표 대신 사유서" 카드 시안 재현
// ─────────────────────────────────────────────────────────────────────────────
function buildHoverMarkdown(ctx: EditorContext, r: BlameResult): vscode.MarkdownString {
    const md = new vscode.MarkdownString(undefined, true);
    md.isTrusted = true;
    md.supportThemeIcons = true;
    md.supportHtml = true;

    const fileName = ctx.filePath.split(/[\\/]/).pop() ?? ctx.filePath;
    const shortHash = (r.commitHash || '').slice(0, 7);
    const specRef = r.specRef ?? '기획서';

    const commitArgs = encodeURIComponent(JSON.stringify([{ commitHash: r.commitHash, repoPath: ctx.repoPath }]));
    const pinArgs = encodeURIComponent(JSON.stringify([{ filePath: ctx.filePath, line: ctx.line }]));
    const isPinned = pinned.has(cacheKey(ctx.filePath, ctx.line));

    // ── 헤더: 카드 타이틀(이름표 대신 사유서) + 파일:라인 ─────────────
    md.appendMarkdown(
        `<span style="color:#A78BFA;font-size:13px;font-weight:700">` +
        `$(sparkle)&nbsp;이름표 대신 사유서</span>\n\n` +
        `<span style="opacity:0.55;font-size:11px">${escapeMd(fileName)} : L${ctx.line}</span>\n\n`,
    );

    md.appendMarkdown(`---\n\n`);

    // ── 본문 내러티브: 작성자·날짜·인용을 한 문장으로 ───────────────
    md.appendMarkdown(`${formatNarrative(r)}\n\n`);

    // ── 칩 행: 커밋 · 티켓 · 기획서(primary) · 팀 ─────────────────────
    const chips: string[] = [];
    if (shortHash) { chips.push(chip('$(git-branch)', shortHash)); }
    if (r.ticket) { chips.push(chip('$(tag)', r.ticket)); }
    if (r.specRef) { chips.push(chip('$(file-text)', r.specRef, { primary: true })); }
    if (r.team) { chips.push(chip('$(organization)', r.team)); }
    if (chips.length) {
        md.appendMarkdown(chips.join('&nbsp;&nbsp;') + `\n\n`);
    }

    // ── AI 추론 ──────────────────────────────────────────────────────
    if (r.aiSuggestion) {
        md.appendMarkdown(
            `<span style="color:#67E8F9;font-size:12px;font-weight:600">` +
            `$(sparkle)&nbsp;AI 추론</span>\n\n` +
            `<span style="opacity:0.85">${escapeMd(r.aiSuggestion)}</span>\n\n`,
        );
    }

    md.appendMarkdown(`---\n\n`);

    // ── Primary CTA: 기획서 §X.X 열기 ────────────────────────────────
    md.appendMarkdown(
        `<span style="background:#0E7490;color:#fff;padding:4px 10px;border-radius:6px;font-weight:600">` +
        `[$(file-text)&nbsp;${escapeMd(specRef)} 열기](command:codewhy.requirementTrace)` +
        `</span>\n\n`,
    );

    // ── 보조 액션: 커밋 / 히스토리 / 고정 ───────────────────────────
    md.appendMarkdown(
        `<span style="opacity:0.65;font-size:11px">` +
        `[$(git-commit) 커밋 보기](command:codewhy.blame.openCommit?${commitArgs})` +
        `&nbsp;·&nbsp;[$(history) 히스토리](command:codewhy.timelineSummary)` +
        `&nbsp;·&nbsp;[$(${isPinned ? 'pinned' : 'pin'}) ${isPinned ? '고정 해제' : '고정'} \`⌘B\`](command:codewhy.blame.pin?${pinArgs})` +
        `</span>`,
    );

    return md;
}

const escapeMd = (s: string) => s.replace(/[\\`*_{}[\]()#+\-.!|]/g, '\\$&');
const escapeHtml = (s: string) =>
    s.replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]!));

/**
 * WebView 카드 HTML — 시안 "이름표 대신 사유서" 디자인 그대로.
 *
 * 디자인 토큰:
 *   - 배경:        #0F0F12 (페이지) / #18181B (카드)
 *   - 테두리:      #27272A
 *   - 포어그라운드: #FAFAFA / #A1A1AA (서브)
 *   - 강조 1:      cyan #67E8F9 (AI 추론, 타이틀 sparkle)
 *   - 강조 2:      teal→violet 그라데이션 #0E7490 → #6D28D9 (chip primary, CTA)
 *   - 인용 코드:   배경 #3F3F46 + 텍스트 #FDE047
 */
function renderCardHtml(ctx: EditorContext, r: BlameResult): string {
    const fileName = ctx.filePath.split(/[\\/]/).pop() ?? ctx.filePath;
    const shortHash = (r.commitHash || '').slice(0, 7);
    const specRef = r.specRef ?? '기획서';

    // 인용된 따옴표 텍스트와 식별자(스네이크/캐멀/대문자)를 코드 강조
    const decorate = (s: string) =>
        escapeHtml(s)
            .replace(/&quot;([^&]+?)&quot;/g, '<code>$1</code>')
            .replace(/\b([A-Z][a-zA-Z]*\.[A-Z_]+)\b/g, '<code>$1</code>')
            .replace(/(?<![\w.])(\d+\.\d+|0\.\d+)(?![\w.])/g, '<code>$1</code>');

    const narrative = decorate(formatNarrative(r));
    const aiBody = r.aiSuggestion ? decorate(r.aiSuggestion) : '';

    const chip = (icon: string, label: string, primary = false) => `
        <span class="chip${primary ? ' chip--primary' : ''}">
            <span class="chip__icon">${icon}</span>${escapeHtml(label)}
        </span>`;

    const chips = [
        shortHash ? chip(svgGitBranch(), shortHash) : '',
        r.ticket ? chip(svgTag(), r.ticket) : '',
        r.specRef ? chip(svgDoc(), r.specRef, true) : '',
        r.team ? chip(svgUsers(), r.team) : '',
    ].filter(Boolean).join('');

    const aiBlock = r.aiSuggestion ? `
        <div class="ai-block">
            <div class="ai-heading">${svgSparkle()} AI 추론</div>
            <div class="ai-body">${aiBody}</div>
        </div>` : '';

    return /* html */ `
<!doctype html>
<html><head><meta charset="utf-8" />
<style>
    *, *::before, *::after { box-sizing: border-box; }
    html, body { margin: 0; padding: 0; background: transparent; }
    body {
        font-family: var(--vscode-font-family);
        color: #FAFAFA;
        padding: 16px;
    }
    .card {
        background: #18181B;
        border: 1px solid #27272A;
        border-radius: 14px;
        padding: 18px 20px 16px;
        max-width: 460px;
        box-shadow: 0 12px 32px rgba(0,0,0,0.45);
    }
    .header {
        display: flex;
        align-items: flex-start;
        justify-content: space-between;
        gap: 12px;
        margin-bottom: 14px;
    }
    .title-block { display: flex; flex-direction: column; gap: 4px; min-width: 0; }
    .title {
        display: flex; align-items: center; gap: 6px;
        color: #FAFAFA; font-size: 14px; font-weight: 700; letter-spacing: -0.01em;
    }
    .title .sparkle { color: #67E8F9; display: inline-flex; }
    .subtitle {
        color: #71717A; font-size: 11px;
        font-family: var(--vscode-editor-font-family, monospace);
    }
    .header-actions { display: flex; gap: 2px; flex-shrink: 0; }
    .icon-btn {
        background: transparent; border: none; cursor: pointer;
        color: #71717A; padding: 4px; border-radius: 6px;
        display: inline-flex; align-items: center; justify-content: center;
        transition: background 0.12s, color 0.12s;
    }
    .icon-btn:hover { background: #27272A; color: #FAFAFA; }
    .narrative {
        color: #E4E4E7; font-size: 13px; line-height: 1.65;
        margin-bottom: 14px;
    }
    .narrative code, .ai-body code {
        background: #3F3F46; color: #FDE047;
        padding: 1px 5px; border-radius: 4px;
        font-family: var(--vscode-editor-font-family, monospace);
        font-size: 11.5px;
    }
    .chips {
        display: flex; gap: 7px; flex-wrap: wrap;
        margin-bottom: 14px;
    }
    .chip {
        display: inline-flex; align-items: center; gap: 5px;
        background: #27272A; color: #A1A1AA;
        padding: 4px 10px; border-radius: 6px;
        font-size: 11px; font-weight: 500;
    }
    .chip__icon { display: inline-flex; opacity: 0.85; }
    .chip--primary {
        background: linear-gradient(135deg, #0E7490 0%, #6D28D9 100%);
        color: #FFFFFF; font-weight: 600;
    }
    .chip--primary .chip__icon { opacity: 1; }
    .ai-block {
        background: #0F0F12;
        border: 1px solid #27272A;
        border-radius: 10px;
        padding: 11px 13px;
        margin-bottom: 14px;
    }
    .ai-heading {
        display: flex; align-items: center; gap: 5px;
        color: #67E8F9; font-size: 12px; font-weight: 600;
        margin-bottom: 6px;
    }
    .ai-body { color: #D4D4D8; font-size: 12px; line-height: 1.55; }
    .cta {
        display: flex; align-items: center; justify-content: space-between;
        width: 100%;
        background: linear-gradient(135deg, #0E7490 0%, #6D28D9 100%);
        color: #FFFFFF; border: none;
        padding: 11px 14px; border-radius: 10px;
        font-size: 13px; font-weight: 600;
        cursor: pointer; font-family: inherit;
        transition: filter 0.12s;
    }
    .cta:hover { filter: brightness(1.12); }
    .cta__label { display: inline-flex; align-items: center; gap: 7px; }
    .cta__more {
        background: rgba(0,0,0,0.22); border-radius: 6px;
        padding: 2px 7px; font-size: 14px; line-height: 1;
    }
    .secondary {
        display: flex; gap: 14px;
        margin-top: 12px;
        font-size: 11px; color: #71717A;
    }
    .secondary a {
        color: #71717A; text-decoration: none; cursor: pointer;
        display: inline-flex; align-items: center; gap: 4px;
    }
    .secondary a:hover { color: #D4D4D8; }
</style></head>
<body>
    <div class="card">
        <div class="header">
            <div class="title-block">
                <div class="title">
                    <span class="sparkle">${svgSparkle()}</span>
                    이름표 대신 사유서
                </div>
                <div class="subtitle">${escapeHtml(fileName)} : L${ctx.line}</div>
            </div>
            <div class="header-actions">
                <button class="icon-btn" data-action="copy" title="내용 복사">${svgCopy()}</button>
                <button class="icon-btn" data-action="close" title="닫기">${svgClose()}</button>
            </div>
        </div>

        <div class="narrative">${narrative}</div>

        <div class="chips">${chips}</div>

        ${aiBlock}

        <button class="cta" data-action="openSpec">
            <span class="cta__label">${svgDoc()} ${escapeHtml(specRef)} 열기</span>
            <span class="cta__more">⋯</span>
        </button>

        <div class="secondary">
            <a data-action="openCommit">${svgGitCommit()} 커밋 보기</a>
            <a data-action="openHistory">${svgHistory()} 히스토리</a>
            <a data-action="togglePin">${svgPin()} 고정 (⌘B)</a>
        </div>
    </div>

<script>
    const vscode = acquireVsCodeApi();
    document.body.addEventListener('click', e => {
        const el = e.target.closest('[data-action]');
        if (!el) return;
        vscode.postMessage({ type: el.dataset.action });
    });
</script>
</body></html>`;
}

// ─── 인라인 SVG 아이콘 (codicon 못 쓰는 WebView용) ────────────────────────────
const svgSparkle = () => `<svg width="13" height="13" viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg"><path d="M8 1l1.5 4.5L14 7l-4.5 1.5L8 13l-1.5-4.5L2 7l4.5-1.5L8 1z" fill="currentColor"/></svg>`;
const svgGitBranch = () => `<svg width="11" height="11" viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg"><path d="M5 3a1.5 1.5 0 1 0-2 0v8a1.5 1.5 0 1 0 1 0V8h3a3 3 0 0 0 3-3V4.92a1.5 1.5 0 1 0-1 0V5a2 2 0 0 1-2 2H4V3z" fill="currentColor"/></svg>`;
const svgTag = () => `<svg width="11" height="11" viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg"><path d="M2 2v6l7 7 6-6-7-7H2zm3 4a1 1 0 1 1 0-2 1 1 0 0 1 0 2z" fill="currentColor"/></svg>`;
const svgDoc = () => `<svg width="11" height="11" viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg"><path d="M3 1h7l3 3v11H3V1zm6 0v4h4M5 8h6M5 10h6M5 12h4" stroke="currentColor" stroke-width="1.2" fill="none"/></svg>`;
const svgUsers = () => `<svg width="11" height="11" viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg"><path d="M5.5 7a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5zm5 0a2 2 0 1 0 0-4 2 2 0 0 0 0 4zM1 14c0-2.5 2-4.5 4.5-4.5S10 11.5 10 14H1zm9-.5c0-1.5.7-2.8 1.8-3.6 2 .3 3.2 1.7 3.2 3.6h-5z" fill="currentColor"/></svg>`;
const svgCopy = () => `<svg width="13" height="13" viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg"><rect x="5" y="5" width="9" height="9" rx="1.5" stroke="currentColor" stroke-width="1.3"/><path d="M2 11V3.5A1.5 1.5 0 0 1 3.5 2H11" stroke="currentColor" stroke-width="1.3" fill="none"/></svg>`;
const svgClose = () => `<svg width="13" height="13" viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg"><path d="M3 3l10 10M13 3L3 13" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg>`;
const svgGitCommit = () => `<svg width="11" height="11" viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg"><circle cx="8" cy="8" r="2.5" stroke="currentColor" stroke-width="1.3" fill="none"/><path d="M0 8h5M11 8h5" stroke="currentColor" stroke-width="1.3"/></svg>`;
const svgHistory = () => `<svg width="11" height="11" viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg"><path d="M8 2a6 6 0 1 1-5.65 4M2 2v4h4M8 5v3l2 2" stroke="currentColor" stroke-width="1.3" fill="none" stroke-linecap="round"/></svg>`;
const svgPin = () => `<svg width="11" height="11" viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg"><path d="M10 1l5 5-2 2-1.5-.5L8 11l-.5 1.5L2 14l1.5-5.5L5 8l3.5-3.5L8 3l2-2z" fill="currentColor"/></svg>`;

/**
 * 4종 칩(commit/ticket/spec/team)의 공통 렌더러.
 * primary=true 인 칩은 강조 색상(시안의 teal 그라데이션 자리)으로 표시한다.
 */
function chip(icon: string, label: string, opts?: { primary?: boolean }): string {
    const bg = opts?.primary ? '#0E7490' : '#27272A';
    const color = opts?.primary ? '#FFFFFF' : '#A1A1AA';
    const weight = opts?.primary ? '600' : '500';
    return (
        `<span style="background:${bg};color:${color};padding:3px 8px;` +
        `border-radius:4px;font-size:11px;font-weight:${weight}">` +
        `${icon}&nbsp;${escapeMd(label)}</span>`
    );
}

/**
 * BlameResult → 카드 본문에 들어갈 한 줄 내러티브.
 *
 * 시안 예시:
 *   "홍길동님이 3월 15일에 \"해외 결제 시 수수료 3% 적용\"이라는
 *    기획 내용을 반영하기 위해 이 코드를 추가했습니다."
 *
 * TODO(개발자 A) — 여기를 채워주세요:
 *   1) r.explanation 안에 "..." 따옴표 인용이 있으면 그대로 살려 인용 처리
 *   2) 날짜는 "3월 15일"처럼 연도 생략 + 한국식 표기
 *   3) "님이/이/가", "을/를" 같은 조사는 받침 유무로 분기
 *   4) 인용이 없을 땐 explanation 본문을 자연스럽게 흘려넣기
 *   분량: 5~10줄.
 */
function formatNarrative(r: BlameResult): string {
    // 임시 폴백 — 시안 톤이 안 살아납니다. 위 TODO를 채우면 카드가 시안처럼 보입니다.
    return `${escapeMd(r.author)}님이 ${escapeMd(formatDisplayDate(r.date))}에 ${r.explanation.replace(/"([^"]+)"/g, '`$1`')}`;
}

// ─────────────────────────────────────────────────────────────────────────────
// 5. 보조 명령
// ─────────────────────────────────────────────────────────────────────────────
function updateStatusBar(line: number, r: BlameResult) {
    if (!statusBar) { return; }
    statusBar.text = `$(sparkle) AI Cop · 라인 ${line} 분석 완료`;
    statusBar.tooltip = `${r.author} · ${formatDisplayDate(r.date)}`;
    statusBar.show();
}

async function openCommitInTerminal(args: { commitHash: string; repoPath: string }) {
    if (!args?.commitHash) {
        vscode.window.showWarningMessage('CodeWhy: 커밋 해시가 없습니다.');
        return;
    }
    const terminal = vscode.window.createTerminal({
        name: `git show ${args.commitHash.slice(0, 7)}`,
        cwd: args.repoPath || undefined,
    });
    terminal.sendText(`git show ${args.commitHash}`);
    terminal.show();
}

async function togglePin(args?: { filePath: string; line: number }) {
    const editor = vscode.window.activeTextEditor;
    if (!editor) { return; }
    const filePath = args?.filePath ?? editor.document.uri.fsPath;
    const line = args?.line ?? editor.selection.active.line + 1;
    const key = cacheKey(filePath, line);

    if (!blameCache.has(key)) {
        vscode.window.showInformationMessage('CodeWhy: 먼저 🔍 렌즈를 클릭해 이 라인을 분석해주세요.');
        return;
    }
    pinned.has(key) ? pinned.delete(key) : pinned.add(key);
    refreshPinnedDecorations(editor);
}

function refreshPinnedDecorations(editor: vscode.TextEditor) {
    if (!pinDecoration) { return; }
    const decorations: vscode.DecorationOptions[] = [];
    for (const key of pinned) {
        const sep = key.lastIndexOf(':');
        const fp = key.slice(0, sep);
        const line = parseInt(key.slice(sep + 1), 10) - 1;
        if (fp !== editor.document.uri.fsPath || line < 0 || line >= editor.document.lineCount) { continue; }
        const entry = blameCache.get(key);
        if (!entry) { continue; }
        decorations.push({
            range: editor.document.lineAt(line).range,
            renderOptions: {
                after: {
                    contentText: `  📌 ${entry.result.author} · ${formatDisplayDate(entry.result.date)} · ${truncate(entry.result.explanation, 55)}`,
                },
            },
        });
    }
    editor.setDecorations(pinDecoration, decorations);
}

// ─────────────────────────────────────────────────────────────────────────────
// 6. 날짜 유틸
// ─────────────────────────────────────────────────────────────────────────────
function formatDisplayDate(s: string): string {
    const d = parseDateLoose(s);
    if (!d) { return s; }
    return d.toLocaleDateString('ko-KR', { year: 'numeric', month: 'long', day: 'numeric' });
}

function formatRelativeKo(s: string): string {
    const d = parseDateLoose(s);
    if (!d) { return ''; }
    const sec = Math.floor((Date.now() - d.getTime()) / 1000);
    if (sec < 60) { return '방금'; }
    const min = Math.floor(sec / 60);
    if (min < 60) { return `${min}분 전`; }
    const hour = Math.floor(min / 60);
    if (hour < 24) { return `${hour}시간 전`; }
    const day = Math.floor(hour / 24);
    if (day < 7) { return `${day}일 전`; }
    const week = Math.floor(day / 7);
    if (week < 5) { return `${week}주 전`; }
    const month = Math.floor(day / 30);
    if (month < 12) { return `${month}개월 전`; }
    return `${Math.floor(day / 365)}년 전`;
}

function parseDateLoose(s: string): Date | null {
    if (!s) { return null; }
    if (/^\d{9,11}$/.test(s)) { return new Date(Number(s) * 1000); }
    const direct = new Date(s);
    if (!isNaN(direct.getTime())) { return direct; }
    const m = s.match(/(\d{4})\D+(\d{1,2})\D+(\d{1,2})/);
    if (m) { return new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3])); }
    return null;
}

const truncate = (s: string, n: number) => s.length > n ? `${s.slice(0, n - 1)}…` : s;
