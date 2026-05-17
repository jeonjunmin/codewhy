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
    triggerHoverAtLine(ctx.filePath, ctx.line);
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
    triggerHoverAtLine(args.filePath, args.line);
}

// ─────────────────────────────────────────────────────────────────────────────
// 3. 에디터 위 호버 팝업 — 커서를 해당 라인으로 이동 후 강제 표시
// ─────────────────────────────────────────────────────────────────────────────
function triggerHoverAtLine(filePath: string, line: number) {
    const editor = vscode.window.activeTextEditor;
    if (!editor || editor.document.uri.fsPath !== filePath) { return; }
    const pos = new vscode.Position(line - 1, 0);
    editor.selection = new vscode.Selection(pos, pos);
    editor.revealRange(
        new vscode.Range(pos, pos),
        vscode.TextEditorRevealType.InCenterIfOutsideViewport,
    );
    // 캐시가 확실히 존재하므로 HoverProvider가 즉시 렌더링
    vscode.commands.executeCommand('editor.action.showHover');
}

// ─────────────────────────────────────────────────────────────────────────────
// 4. 호버 마크다운 빌더 — 디자인 시안 재현
// ─────────────────────────────────────────────────────────────────────────────
function buildHoverMarkdown(ctx: EditorContext, r: BlameResult): vscode.MarkdownString {
    const md = new vscode.MarkdownString(undefined, true);
    md.isTrusted = true;
    md.supportThemeIcons = true;
    md.supportHtml = true;

    const initial = r.author.slice(0, 1);
    const shortHash = (r.commitHash || '').slice(0, 7);
    const displayDate = formatDisplayDate(r.date);
    const relative = formatRelativeKo(r.date);
    // 따옴표로 묶인 텍스트 → 코드스팬으로 강조
    const highlighted = r.explanation.replace(/"([^"]+)"/g, '`$1`');

    const commitArgs = encodeURIComponent(JSON.stringify([{ commitHash: r.commitHash, repoPath: ctx.repoPath }]));
    const pinArgs = encodeURIComponent(JSON.stringify([{ filePath: ctx.filePath, line: ctx.line }]));
    const isPinned = pinned.has(cacheKey(ctx.filePath, ctx.line));

    // ── 헤더: 배지 + 라인 번호 ──────────────────────────────────────
    md.appendMarkdown(
        `<span style="background:#2563EB;color:#fff;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:700">` +
        `● Context Blame</span>&nbsp;&nbsp;` +
        `<span style="opacity:0.6;font-size:12px">라인 ${ctx.line}</span>\n\n`,
    );

    md.appendMarkdown(`---\n\n`);

    // ── 작성자 행 ────────────────────────────────────────────────────
    md.appendMarkdown(
        `\`${initial}\`&nbsp;&nbsp;` +
        `**${escapeMd(r.author)}**` +
        `&nbsp;&nbsp;<span style="opacity:0.65">${escapeMd(displayDate)}` +
        (relative ? `&nbsp;·&nbsp;${escapeMd(relative)}` : '') +
        `</span>` +
        (shortHash ? `&nbsp;&nbsp;$(git-branch)&nbsp;\`${shortHash}\`` : '') +
        `\n\n`,
    );

    // ── 설명 ─────────────────────────────────────────────────────────
    md.appendMarkdown(`${highlighted}\n\n`);

    md.appendMarkdown(`---\n\n`);

    // ── 액션 버튼 ────────────────────────────────────────────────────
    md.appendMarkdown(
        `[$(file-text) 기획서 열기](command:codewhy.requirementTrace)` +
        `&nbsp;&nbsp;[$(git-commit) 커밋 보기](command:codewhy.blame.openCommit?${commitArgs})` +
        `&nbsp;&nbsp;[$(history) 히스토리](command:codewhy.timelineSummary)` +
        `&nbsp;&nbsp;&nbsp;&nbsp;[$(${isPinned ? 'pinned' : 'pin'}) ${isPinned ? '고정 해제' : '고정'} \`⌘B\`](command:codewhy.blame.pin?${pinArgs})`,
    );

    return md;
}

const escapeMd = (s: string) => s.replace(/[\\`*_{}[\]()#+\-.!|]/g, '\\$&');

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
