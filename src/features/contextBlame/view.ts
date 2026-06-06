import * as vscode from 'vscode';
import { EditorContext } from '../../shared/editor';
import { BlameResult } from '../../shared/types';
import { askBlame, fetchContextBlame } from './api';
import { ContextBlameSidebarProvider, VIEW_ID } from './sidebar';

/**
 * Context Blame UI 레이어 — 사이드바(WebviewView) 와 에디터 보조 UI 를 연결한다.
 *
 * 동작 흐름:
 *  1. 확장 활성화 → registerContextBlameCodeLens()
 *     · CodeLens (커서 라인에 🔍 렌즈)
 *     · HoverProvider (분석 끝난 라인에 짧은 마크다운 팝업)
 *     · 사이드바 Provider (CONTEXT BLAME 패널)
 *  2. 렌즈 클릭 → 백엔드 분석 → 사이드바에 결과 push
 *
 * 호버는 사이드바와 별개로 "이 라인 분석되어 있음" 확인용 라이트 카드.
 * 풀-디자인은 사이드바가 담당.
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
let sidebar: ContextBlameSidebarProvider | undefined;
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
    pushToSidebar(ctx, result);
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

    // ── 사이드바 Provider 등록 ─────────────────────────────────────
    sidebar = new ContextBlameSidebarProvider(context.extensionUri, {
        onOpenSpec: (sourceRef, issueUrl) => openSpecDocument(sourceRef, issueUrl),
        onOpenCommit: (commitHash, repoPath) =>
            vscode.commands.executeCommand('codewhy.blame.openCommit', { commitHash, repoPath }),
        onOpenHistory: () => vscode.commands.executeCommand('codewhy.timelineSummary'),
        onTogglePin: (filePath, line) =>
            vscode.commands.executeCommand('codewhy.blame.pin', { filePath, line }),
        onAskAi: (question, ctx, result) => handleAskAi(question, ctx, result),
        onCopy: (text) => {
            vscode.env.clipboard.writeText(text);
            vscode.window.setStatusBarMessage('CodeWhy: 카드 내용을 클립보드에 복사했어요.', 2000);
        },
    });
    context.subscriptions.push(
        vscode.window.registerWebviewViewProvider(VIEW_ID, sidebar, {
            webviewOptions: { retainContextWhenHidden: true },
        }),
    );

    // ── HoverProvider — 캐시에 분석 결과가 있는 라인만 짧은 마크다운 팝업
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

    // ── CodeLens — 커서가 있는 라인에만 🔍 렌즈 표시
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

    // ── 문서 수정 감지 → 해당 파일의 블레임 캐시·핀 무효화
    //    줄이 밀리면 캐시 키(filePath:line)가 엉뚱한 줄을 가리키므로 통째로 지운다.
    context.subscriptions.push(
        vscode.workspace.onDidChangeTextDocument(e => {
            if (e.contentChanges.length === 0) { return; }
            const changedPath = e.document.uri.fsPath;
            invalidateFileCache(changedPath);
        }),
    );

    // ── 커서 이동 감지 → CodeLens 위치 갱신 + 캐시 있으면 사이드바 자동 갱신
    context.subscriptions.push(
        vscode.window.onDidChangeTextEditorSelection(e => {
            const newLine = e.selections[0].active.line;
            const newPath = e.textEditor.document.uri.fsPath;
            if (newLine !== currentCursorLine || newPath !== currentFilePath) {
                currentCursorLine = newLine;
                currentFilePath = newPath;
                codeLensEmitter!.fire();

                // 이미 분석된 라인이면 사이드바도 그 라인으로 따라간다
                const entry = blameCache.get(cacheKey(newPath, newLine + 1));
                if (entry) { pushToSidebar(entry.ctx, entry.result); }
            }
        }),
        vscode.window.onDidChangeActiveTextEditor(editor => {
            if (editor) { refreshPinnedDecorations(editor); }
        }),
    );

    // ── 보조 명령 등록
    context.subscriptions.push(
        vscode.commands.registerCommand('codewhy.blame.analyzeAndShow', handleAnalyzeAndShow),
        vscode.commands.registerCommand('codewhy.blame.openCommit', openCommitInTerminal),
        vscode.commands.registerCommand('codewhy.blame.pin', togglePin),
    );
}

// ─────────────────────────────────────────────────────────────────────────────
// 2. CodeLens 클릭 → 분석 → 사이드바 push
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
    pushToSidebar(entry.ctx, entry.result);
}

function pushToSidebar(ctx: EditorContext, r: BlameResult) {
    if (!sidebar) { return; }
    const isPinned = pinned.has(cacheKey(ctx.filePath, ctx.line));
    sidebar.setBlame(ctx, r, isPinned);
}

// ─────────────────────────────────────────────────────────────────────────────
// 3. 호버 마크다운 — "이 라인 분석됨" 라이트 카드
// ─────────────────────────────────────────────────────────────────────────────
function buildHoverMarkdown(ctx: EditorContext, r: BlameResult): vscode.MarkdownString {
    const md = new vscode.MarkdownString(undefined, true);
    md.isTrusted = true;
    md.supportThemeIcons = true;
    md.supportHtml = true;

    const fileName = ctx.filePath.split(/[\\/]/).pop() ?? ctx.filePath;
    const date = formatDisplayDate(r.date);

    md.appendMarkdown(
        `<span style="color:#A78BFA;font-weight:700">$(sparkle) ${escapeMd(r.author)}</span>` +
        `&nbsp;·&nbsp;<span style="opacity:0.65">${escapeMd(date)}</span>` +
        `&nbsp;·&nbsp;<span style="opacity:0.55;font-size:11px">${escapeMd(fileName)}:L${ctx.line}</span>\n\n` +
        `${escapeMd(r.explanation)}\n\n` +
        `[$(arrow-right) CONTEXT BLAME 사이드바에서 자세히](command:codewhy.contextBlame.focus)`,
    );
    return md;
}

const escapeMd = (s: string) => s.replace(/[\\`*_{}[\]()#+\-.!|]/g, '\\$&');

// ─────────────────────────────────────────────────────────────────────────────
// 4. 보조 명령
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
    if (!editor && !args) { return; }
    const filePath = args?.filePath ?? editor!.document.uri.fsPath;
    const line = args?.line ?? editor!.selection.active.line + 1;
    const key = cacheKey(filePath, line);

    if (!blameCache.has(key)) {
        vscode.window.showInformationMessage('CodeWhy: 먼저 🔍 렌즈를 클릭해 이 라인을 분석해주세요.');
        return;
    }
    pinned.has(key) ? pinned.delete(key) : pinned.add(key);
    if (editor) { refreshPinnedDecorations(editor); }
    sidebar?.refreshPinned(pinned.has(key));
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
// 5. AI 후속 질문 — 사이드바 인풋에서 Enter 친 경우
// ─────────────────────────────────────────────────────────────────────────────
// ─────────────────────────────────────────────────────────────────────────────
// 5-b. 출처 열기 — 우선순위:
//      (1) issueUrl 이 있으면 연관 GitHub Issue 페이지를 브라우저로 연다
//      (2) sourceRef 에서 로컬 기획서 파일명을 뽑을 수 있으면 documentPaths 에서 찾아 연다
//      (3) 둘 다 실패하면 요구사항 역추적으로 폴백
// ─────────────────────────────────────────────────────────────────────────────
async function openSpecDocument(sourceRef: string | null, issueUrl: string | null) {
    if (issueUrl) {
        await vscode.env.openExternal(vscode.Uri.parse(issueUrl));
        return;
    }
    const fileName = extractSpecFileName(sourceRef);
    if (fileName) {
        const uri = await findDocumentUri(fileName);
        if (uri) {
            await vscode.commands.executeCommand('vscode.open', uri);
            if (/§/.test(sourceRef ?? '')) {
                vscode.window.setStatusBarMessage(`CodeWhy: ${sourceRef} 로 이동했어요 (섹션은 문서에서 확인).`, 4000);
            }
            return;
        }
        vscode.window.showWarningMessage(
            `CodeWhy: "${fileName}" 문서를 documentPaths 설정에서 찾지 못했어요. 요구사항 역추적으로 대신 검색합니다.`,
        );
    }
    // 파일명을 못 뽑았거나 문서를 못 찾으면 기존 요구사항 역추적으로 폴백
    vscode.commands.executeCommand('codewhy.requirementTrace');
}

/** "2026_결제_기획서.pdf §4.2" → "2026_결제_기획서.pdf" */
function extractSpecFileName(sourceRef: string | null): string | null {
    if (!sourceRef) { return null; }
    const m = sourceRef.match(/[^\s§]+\.(pdf|docx|xlsx|md|txt)/i);
    return m ? m[0] : null;
}

/** documentPaths 설정의 폴더들에서 파일명이 일치하는 문서를 찾는다. */
async function findDocumentUri(fileName: string): Promise<vscode.Uri | undefined> {
    const folders = vscode.workspace.getConfiguration('codewhy').get<string[]>('documentPaths') ?? [];
    for (const folder of folders) {
        const pattern = new vscode.RelativePattern(folder, `**/${fileName}`);
        const hits = await vscode.workspace.findFiles(pattern, undefined, 1);
        if (hits.length) { return hits[0]; }
    }
    return undefined;
}

async function handleAskAi(question: string, ctx: EditorContext, _result: BlameResult) {
    if (!sidebar) { return; }
    sidebar.showAnswer(question, '…'); // 즉시 질문 버블 + 로딩 표시
    try {
        const { answer } = await askBlame({
            filePath: ctx.filePath,
            line: ctx.line,
            repoPath: ctx.repoPath,
            question,
        });
        sidebar.showAnswer(question, answer);
    } catch (err) {
        sidebar.showAnswer(question, `답변을 가져오지 못했어요: ${(err as Error).message}`);
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// 6. 날짜 유틸
// ─────────────────────────────────────────────────────────────────────────────
function formatDisplayDate(s: string): string {
    const d = parseDateLoose(s);
    if (!d) { return s; }
    return d.toLocaleDateString('ko-KR', { year: 'numeric', month: 'long', day: 'numeric' });
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

// ─────────────────────────────────────────────────────────────────────────────
// 7. 캐시 무효화 — 파일 편집 시 줄 번호가 밀려 캐시가 stale 해지는 것을 방지
// ─────────────────────────────────────────────────────────────────────────────
function invalidateFileCache(filePath: string) {
    for (const key of [...blameCache.keys()]) {
        if (key.startsWith(filePath + ':')) {
            blameCache.delete(key);
        }
    }
    for (const key of [...pinned]) {
        if (key.startsWith(filePath + ':')) {
            pinned.delete(key);
        }
    }
    const editor = vscode.window.activeTextEditor;
    if (editor && editor.document.uri.fsPath === filePath) {
        refreshPinnedDecorations(editor);
    }
}
