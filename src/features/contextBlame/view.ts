import { execSync } from 'child_process';
import * as vscode from 'vscode';
import { EditorContext, getEditorContext } from '../../shared/editor';
import * as localGit from '../../shared/git';
import { BlameRequest, BlameResult, CommitInput, ReasonRequest, TraceRequest } from '../../shared/types';
import { fetchRequirementTrace } from '../requirementTrace/api';
import { streamTimelineSummary } from '../timelineSummary/api';
import { fetchCommitReason, streamContextBlame } from './api';
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

/** `codewhy.*` 설정값을 읽는다(미설정 시 fallback). 토글 게이트용 헬퍼. */
const cfg = <T>(key: string, fallback: T): T =>
    vscode.workspace.getConfiguration('codewhy').get<T>(key, fallback);

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
        onOpenCommit: (commitHash, repoPath) =>
            vscode.commands.executeCommand('codewhy.blame.openCommit', { commitHash, repoPath }),
        onSwitchTab: (tab) => handleSwitchTab(tab),
        onOpenIssue: (url) => { vscode.env.openExternal(vscode.Uri.parse(url)); },
        // 이슈 기능 개발 전까지 '이슈 N' 배지는 임시 안내만 — 실제 이동은 DEVELOPMENT_GUIDE.md TODO 참고.
        onOpenIssueTodo: () => {
            vscode.window.showInformationMessage('연관 이슈 보기는 이슈 기능 연동 후 제공될 예정입니다.');
        },
        // 라인 수정 이력 항목 펼침 → 그 커밋의 변경 사유를 지연 생성해 사이드바에 주입.
        onExpandHistory: (hash, filePath, repoPath) => handleExpandHistory(hash, filePath, repoPath),
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
                if (!cfg('hover.enabled', true)) { return null; }
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
                    if (!cfg('codeLens.enabled', true)) { return []; }
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

    // ── 설정 변경 감지 → CodeLens 토글을 즉시 반영(렌즈 다시 계산)
    //    Hover 는 다음 호버 때 cfg() 를 다시 읽으므로 별도 처리가 필요 없다.
    context.subscriptions.push(
        vscode.workspace.onDidChangeConfiguration(e => {
            if (e.affectsConfiguration('codewhy.codeLens')) { codeLensEmitter?.fire(); }
        }),
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
        // 제목줄(view/title) ⚙ 설정 버튼 — 'CodeWhy' 패널 제목 오른쪽 끝에 노출된다.
        vscode.commands.registerCommand('codewhy.openSettings', () =>
            vscode.commands.executeCommand('workbench.action.openSettings', 'codewhy')),
    );
}

// ─────────────────────────────────────────────────────────────────────────────
// 2. CodeLens 클릭 → 분석 → 사이드바 push
// ─────────────────────────────────────────────────────────────────────────────
async function handleAnalyzeAndShow(args: { filePath: string; line: number; repoPath: string }) {
    const entry = blameCache.get(cacheKey(args.filePath, args.line));
    if (entry) {
        // 캐시 적중 — 스트리밍 없이 즉시 표시.
        updateStatusBar(args.line, entry.result);
        pushToSidebar(entry.ctx, entry.result);
        return;
    }
    // 미스 — SSE 스트리밍으로 설명을 토큰 단위로 그린다(타임라인 패턴과 동일).
    streamBlameInto(args);
}

/**
 * 캐시 미스 라인을 스트리밍 분석해 사이드바에 점진 렌더한다.
 * meta(메타·이력 즉시) → delta(설명 타이핑) → done(출처/PR 확정) 3단으로 흐른다.
 * 알림 스피너 대신 패널 안 캐럿으로 진행을 보여주므로 fire-and-forget 으로 둔다.
 */
function streamBlameInto(args: { filePath: string; line: number; repoPath: string }) {
    if (!sidebar) { return; }
    const ctx = args as EditorContext;
    const key = cacheKey(args.filePath, args.line);
    let metaSeen = false;

    streamContextBlame(buildBlameRequest(args), {
        onMeta: (meta) => { metaSeen = true; sidebar!.blameStreaming(ctx, meta); },
        onDelta: (delta) => sidebar!.blameDelta(delta),
        onDone: (result) => {
            // degraded(Bedrock 폴백)는 일시적 실패이므로 캐싱하지 않는다(다음 분석 때 자동 회복).
            if (!result.aiDegraded) { blameCache.set(key, { ctx, result }); }
            updateStatusBar(args.line, result);
            // meta 를 본 경우(스트리밍)는 콜아웃 확정만, 아니면(캐시 적중/노이즈 JSON) 전체 렌더.
            if (metaSeen) { sidebar!.blameResult(ctx, result); }
            else { pushToSidebar(ctx, result); }
        },
        onError: (message) => vscode.window.showErrorMessage(`Context Blame 실패: ${message}`),
    }).catch((err) => vscode.window.showErrorMessage(`Context Blame 실패: ${(err as Error).message}`));
}

// ─────────────────────────────────────────────────────────────────────────────
// 공통 탭바 — 한 패널 안 세 페인. 탭 클릭 시 현재 커서 라인 기준으로 자동 분석한다.
// 각 탭의 결과는 모두 같은 sidebar provider 의 set*() 메서드로 밀어넣는다.
// ─────────────────────────────────────────────────────────────────────────────
function handleSwitchTab(tab: string) {
    switch (tab) {
        case 'timeline': runTimelineTab(); break;
        case 'issue': runIssueTab(); break;
        case 'blame':
        default: runBlameTab(); break;
    }
}

/** 블레임 탭: 현재 커서 라인을 분석(캐시 적중 시 즉시)해 패널에 표시. */
export function runBlameTab() {
    const ctx = getEditorContext();
    if (!ctx) { return; }
    handleAnalyzeAndShow({ filePath: ctx.filePath, line: ctx.line, repoPath: ctx.repoPath });
}

/** 타임라인 탭: 파일 커밋 이력을 모아 AI 요약을 스트리밍으로 패널에 표시. */
export function runTimelineTab() {
    if (!sidebar) { return; }
    sidebar.activateTab('timeline');
    const ctx = getEditorContext();
    if (!ctx) { return; }

    const fileName = ctx.filePath.split(/[\\/]/).pop() ?? ctx.filePath;
    const commits = collectGitLog(ctx.repoPath, ctx.filePath);
    if (commits.length === 0) {
        sidebar.timelineEmpty('이 파일의 git 커밋 이력을 찾을 수 없습니다.');
        return;
    }

    sidebar.timelineStreaming(fileName);
    streamTimelineSummary(
        { filePath: ctx.filePath, repoPath: ctx.repoPath, commits },
        {
            onDelta: (delta) => sidebar!.timelineDelta(delta),
            onDone: (result) => sidebar!.timelineResult(fileName, result),
            onError: (message) => sidebar!.timelineEmpty(`타임라인 요약 실패: ${message}`),
        },
    ).catch((err) => sidebar!.timelineEmpty(`타임라인 요약 실패: ${(err as Error).message}`));
}

/** 이슈 탭: 현재 라인과 연관된 GitHub Issue 를 역추적해 목록으로 표시. */
export async function runIssueTab() {
    if (!sidebar) { return; }
    sidebar.activateTab('issue');
    const ctx = getEditorContext();
    if (!ctx) { return; }

    sidebar.issueLoading();
    try {
        const result = await fetchRequirementTrace(buildTraceRequest(ctx));
        if (!result.documents || result.documents.length === 0) {
            sidebar.issueEmpty(`L${ctx.line} 와 연관된 GitHub Issue를 찾지 못했습니다.`);
            return;
        }
        sidebar.issueResult(ctx.line, result);
    } catch (err) {
        sidebar.issueEmpty(`요구사항 역추적 실패: ${(err as Error).message}`);
    }
}

/**
 * 현재 라인의 blame 요청 본문을 로컬 git 으로 조립한다.
 *
 * 백엔드가 원격(AWS)에 있으면 사용자 로컬 저장소에 접근할 수 없어 서버에서 git 을 돌릴 수 없다.
 * 그래서 저장소가 있는 이곳에서 blame/브랜치/라인 이력/후속 커밋/remote 를 모아 보낸다.
 */
function buildBlameRequest(ctx: { filePath: string; line: number; repoPath: string }): BlameRequest {
    const { meta, unavailable } = localGit.getBlameInfo(ctx.repoPath, ctx.filePath, ctx.line);
    const branch = localGit.getCurrentBranch(ctx.repoPath);
    const ticket = meta ? localGit.extractTicket(meta.message, branch) : null;
    return {
        filePath: ctx.filePath,
        line: ctx.line,
        repoPath: ctx.repoPath,
        blame: meta,
        unavailable,
        branch,
        lineHistory: localGit.getLineHistory(ctx.repoPath, ctx.filePath, ctx.line),
        followups: localGit.getFollowupCommits(ctx.repoPath, ticket, meta?.commitHash ?? ''),
        remoteUrl: localGit.getRemoteUrl(ctx.repoPath),
    };
}

/** 라인 이력 항목 펼침(/reason)의 요청 본문을 로컬 git 으로 조립한다. */
function buildReasonRequest(hash: string, filePath: string, repoPath: string): ReasonRequest {
    const { meta } = localGit.getCommitInfo(repoPath, filePath, hash);
    const branch = localGit.getCurrentBranch(repoPath);
    const ticket = meta ? localGit.extractTicket(meta.message, branch) : null;
    return {
        filePath,
        repoPath,
        hash,
        commit: meta,
        branch,
        followups: localGit.getFollowupCommits(repoPath, ticket, hash),
        remoteUrl: localGit.getRemoteUrl(repoPath),
    };
}

/** 이슈 역추적(/trace)의 요청 본문을 로컬 git 으로 조립한다. */
function buildTraceRequest(ctx: { filePath: string; line: number; repoPath: string }): TraceRequest {
    const { meta } = localGit.getBlameInfo(ctx.repoPath, ctx.filePath, ctx.line);
    return {
        filePath: ctx.filePath,
        line: ctx.line,
        repoPath: ctx.repoPath,
        blame: meta,
        branch: localGit.getCurrentBranch(ctx.repoPath),
        remoteUrl: localGit.getRemoteUrl(ctx.repoPath),
    };
}

/** 파일의 git 커밋 이력(타임라인 입력)을 수집한다. */
function collectGitLog(repoPath: string, filePath: string): CommitInput[] {
    try {
        const out = execSync(
            `git log --follow --format="%H|%an|%ad|%s" --date=short -- "${filePath}"`,
            { cwd: repoPath, timeout: 10_000 },
        ).toString().trim();
        return out.split('\n').filter(Boolean).map(line => {
            const [hash, author, date, ...rest] = line.split('|');
            return { hash, author, date, subject: rest.join('|') };
        });
    } catch {
        return [];
    }
}

/**
 * 라인 수정 이력 항목을 펼칠 때, 그 커밋의 변경 사유를 백엔드에서 받아 사이드바에 채운다.
 * 백엔드가 (file_id, commit_id) 캐시를 공유하므로 같은 커밋 재펼침은 빠르게 응답한다.
 * 알림 스피너 대신 펼친 행 안에 로딩 문구를 두므로 fire-and-forget 으로 둔다.
 */
async function handleExpandHistory(hash: string, filePath: string, repoPath: string) {
    if (!sidebar) { return; }
    try {
        const { reason } = await fetchCommitReason(buildReasonRequest(hash, filePath, repoPath));
        sidebar.setHistoryReason(hash, reason || '(변경 사유를 찾지 못했습니다.)');
    } catch (err) {
        sidebar.setHistoryReason(hash, `변경 사유를 불러오지 못했습니다: ${(err as Error).message}`);
    }
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
