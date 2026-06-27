import { execFile, execSync } from 'child_process';
import { promisify } from 'util';
import * as vscode from 'vscode';
import { EditorContext, getEditorContext } from '../../shared/editor';
import * as localGit from '../../shared/git';
import { BlameRequest, BlameResult, CommitInput, GitCommitMeta, IssueChatMessage, IssueChatRequest, ReasonRequest, TraceRequest } from '../../shared/types';
import { fetchRequirementTrace } from '../requirementTrace/api';
import { clearTimelineCache, streamTimelineSummary } from '../timelineSummary/api';
import { clearBlameCache, fetchCommitReason, fetchLineTitles, streamContextBlame, streamIssueChat } from './api';
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
        // '라인 수정 이력'의 '이슈 N' 배지 클릭 — 이슈 탭으로 전환 후 그 커밋이 참조한 이슈만 보여준다.
        onOpenCommitIssues: (hash, filePath, repoPath) => handleOpenCommitIssues(hash, filePath, repoPath),
        // 라인 수정 이력 항목 펼침 → 그 커밋의 변경 사유를 지연 생성해 사이드바에 주입.
        onExpandHistory: (hash, filePath, repoPath) => handleExpandHistory(hash, filePath, repoPath),
        // 이슈 상세 'AI 질문' → 현재 이슈를 컨텍스트로 멀티턴 답변을 스트리밍해 사이드바로 중계.
        onIssueChat: (payload) => handleIssueChat(payload),
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
        // 라인 이력 타이틀은 사후에 AI 로 다듬어 진행형 교체(해시별 캐시라 재호출도 저렴).
        void applyLineTitles(entry.result.lineHistory, args);
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
        onMeta: (meta) => {
            metaSeen = true;
            sidebar!.blameStreaming(ctx, meta);
            // 라인 이력 목록은 즉시 떴으니, 타이틀은 사후에 AI 로 다듬어 진행형 교체한다.
            void applyLineTitles(meta.lineHistory, ctx);
        },
        onDelta: (delta) => sidebar!.blameDelta(delta),
        onDone: (result) => {
            // degraded(Bedrock 폴백)는 일시적 실패이므로 캐싱하지 않는다(다음 분석 때 자동 회복).
            if (!result.aiDegraded) { blameCache.set(key, { ctx, result }); }
            updateStatusBar(args.line, result);
            // meta 를 본 경우(스트리밍)는 콜아웃 확정만, 아니면(캐시 적중/노이즈 JSON) 전체 렌더.
            if (metaSeen) { sidebar!.blameResult(ctx, result); }
            else { pushToSidebar(ctx, result); }
        },
        onError: (message) => vscode.window.showErrorMessage(`돋보기 실패: ${message}`),
    }).catch((err) => vscode.window.showErrorMessage(`돋보기 실패: ${(err as Error).message}`));
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
    const commits = collectCommitsWithChanges(ctx.repoPath, ctx.filePath);
    if (commits.length === 0) {
        sidebar.timelineEmpty('이 파일의 git 커밋 이력을 찾을 수 없습니다.');
        return;
    }

    sidebar.timelineStreaming(fileName);
    streamTimelineSummary(
        { filePath: ctx.filePath, repoPath: ctx.repoPath, commits },
        {
            onDelta: (delta) => sidebar!.timelineDelta(delta),
            onSummaryDone: (summary) => sidebar!.timelineSummaryDone(fileName, summary),
            onDone: (result) => sidebar!.timelineResult(fileName, result),
            onError: (message) => sidebar!.timelineEmpty(`타임라인 요약 실패: ${message}`),
        },
    ).catch((err) => sidebar!.timelineEmpty(`타임라인 요약 실패: ${(err as Error).message}`));
}

/**
 * 현재 파일의 타임라인 요약 캐시를 백엔드에서 비운다.
 * 비운 뒤 타임라인을 다시 실행하면 캐시 미스가 나면서 최신 형식으로 재요약된다.
 */
export async function runClearTimelineCache() {
    const ctx = getEditorContext();
    if (!ctx) { return; }

    const fileName = ctx.filePath.split(/[\\/]/).pop() ?? ctx.filePath;
    try {
        const deleted = await clearTimelineCache({ filePath: ctx.filePath, repoPath: ctx.repoPath });
        vscode.window.showInformationMessage(
            deleted > 0
                ? `CodeWhy: ${fileName} 타임라인 캐시를 비웠습니다. 타임라인을 다시 실행하면 최신 형식으로 요약합니다.`
                : `CodeWhy: ${fileName} 에는 비울 타임라인 캐시가 없습니다.`,
        );
    } catch (err) {
        vscode.window.showErrorMessage(`CodeWhy: 타임라인 캐시 비우기 실패 — ${(err as Error).message}`);
    }
}

/**
 * 현재 파일의 돋보기(블레임) 설명 캐시를 비우고 곧장 현재 라인을 재분석한다(시연 재분석용).
 *
 * 캐시는 두 층이다 — 백엔드 blame_explanations + 확장 메모리 blameCache(라인별).
 * 백엔드만 비우면 라인 재분석이 확장 메모리 캐시에 적중해(handleAnalyzeAndShow) 백엔드를
 * 호출하지 않으므로 화면이 그대로다. 그래서 둘 다 비우고 현재 커서 라인을 다시 분석해 즉시 새로 그린다.
 */
export async function runClearBlameCache() {
    const ctx = getEditorContext();
    if (!ctx) { return; }

    const fileName = ctx.filePath.split(/[\\/]/).pop() ?? ctx.filePath;
    try {
        const deleted = await clearBlameCache({ filePath: ctx.filePath, repoPath: ctx.repoPath });

        // 확장 메모리 캐시(현재 파일의 모든 라인)도 함께 비운다 — 안 그러면 재분석이 옛 결과로 적중한다.
        for (const key of [...blameCache.keys()]) {
            if (key.startsWith(ctx.filePath + ':')) { blameCache.delete(key); }
        }

        vscode.window.showInformationMessage(
            `CodeWhy: ${fileName} 돋보기 캐시를 비웠습니다(${deleted}건). 현재 라인을 다시 분석합니다…`,
        );

        // 현재 커서 라인을 즉시 재분석해 패널을 새로 그린다(캐시 미스 → 스트리밍).
        runBlameTab();
    } catch (err) {
        vscode.window.showErrorMessage(`CodeWhy: 돋보기 캐시 비우기 실패 — ${(err as Error).message}`);
    }
}

/** 이슈 탭: 현재 파일과 연관된 GitHub Issue 를 역추적해 목록으로 표시. */
export async function runIssueTab() {
    if (!sidebar) { return; }
    sidebar.activateTab('issue');
    const ctx = getEditorContext();
    if (!ctx) { return; }

    // 추적 단위가 '파일'이므로 빈 결과/결과 표시 모두 파일명을 기준으로 안내한다.
    const issueFileName = ctx.filePath.split(/[\\/]/).pop() ?? ctx.filePath;
    sidebar.issueLoading();
    try {
        const result = await fetchRequirementTrace(buildTraceRequest(ctx));
        if (!result.documents || result.documents.length === 0) {
            sidebar.issueEmpty(`${issueFileName} 와 연관된 GitHub Issue를 찾지 못했습니다.`);
            return;
        }
        sidebar.issueResult(ctx.line, issueFileName, result);
    } catch (err) {
        sidebar.issueEmpty(`요구사항 역추적 실패: ${(err as Error).message}`);
    }
}

/**
 * '라인 수정 이력'의 '이슈 N' 배지 클릭 처리 — 이슈 탭으로 전환하고,
 * 클릭한 그 커밋 '한 건'이 참조하는 이슈만 역추적해 커밋 스코프 목록으로 그린다.
 *
 * 파일 단위 이슈 검색(runIssueTab)과 달리 추적 단위가 '커밋'이라, 백엔드의 단일-커밋
 * 경로(service.trace)를 깨우는 요청을 만든다(buildCommitTraceRequest 참고).
 * 알림 스피너 대신 패널 안 배너+스피너로 진행을 보여주므로 fire-and-forget 으로 둔다.
 */
async function handleOpenCommitIssues(hash: string, filePath: string, repoPath: string) {
    if (!sidebar) { return; }
    sidebar.activateTab('issue');

    const { meta } = localGit.getCommitInfo(repoPath, filePath, hash);
    // 배너에 보일 커밋 제목 — 메시지 첫 줄, 없으면 7자리 해시로 폴백.
    const subject = (meta?.message ?? '').split('\n')[0].trim() || hash.slice(0, 7);
    sidebar.issueCommitLoading(hash, subject);

    if (!meta) {
        sidebar.issueCommitEmpty(hash, subject, '이 커밋의 정보를 찾지 못했습니다.');
        return;
    }
    try {
        const result = await fetchRequirementTrace(buildCommitTraceRequest(filePath, repoPath, meta));
        if (!result.documents || result.documents.length === 0) {
            sidebar.issueCommitEmpty(hash, subject, '이 커밋과 연관된 이슈를 찾지 못했습니다.');
            return;
        }
        sidebar.issueCommitResult(hash, subject, result);
    } catch (err) {
        sidebar.issueCommitEmpty(hash, subject, `커밋 이슈 조회 실패: ${(err as Error).message}`);
    }
}

/**
 * 커밋 스코프 이슈 역추적(/trace)의 요청 본문을 조립한다.
 *
 * ⚠️ 추적 단위가 '커밋'이라는 점이 핵심이다. 라우터는 commits 가 비어 있을 때만
 * 단일-커밋 경로(service.trace)를 타고, commits 가 있으면 파일 전체 이슈를 모으는
 * trace_file 로 빠진다. 따라서 클릭한 커밋만 보여주려면 commits 는 반드시 비우고,
 * 그 커밋 메타를 blame 으로 실어 보낸다.
 */
function buildCommitTraceRequest(filePath: string, repoPath: string, meta: GitCommitMeta): TraceRequest {
    return {
        filePath,
        line: 0,                 // 커밋 스코프라 라인 의미 없음(파일 단위 추적의 잔재 필드)
        repoPath,
        blame: meta,             // 이 커밋 한 건 → 라우터의 단일-커밋 경로(service.trace)
        commits: [],             // 비워서 파일 단위 trace_file 경로를 회피한다
        branch: localGit.getCurrentBranch(repoPath),
        remoteUrl: localGit.getRemoteUrl(repoPath),
    };
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

/**
 * 이슈 역추적(/trace)의 요청 본문을 로컬 git 으로 조립한다.
 *
 * 추적 단위는 '라인'이 아니라 '파일' 이다 — 이 파일을 건드린 커밋 이력 전체를 모아
 * 보내면 백엔드가 각 커밋의 연관 이슈를 합쳐 중복 제거한다. blame(라인 단건)은
 * 커밋 이력이 비었을 때의 폴백으로만 남긴다.
 */
function buildTraceRequest(ctx: { filePath: string; line: number; repoPath: string }): TraceRequest {
    const { meta } = localGit.getBlameInfo(ctx.repoPath, ctx.filePath, ctx.line);
    // GitHub API 호출량을 제어하려 최근 커밋으로 상한을 둔다(파일이 오래될수록 이력이 길어짐).
    const commits = collectGitLog(ctx.repoPath, ctx.filePath).slice(0, TRACE_COMMIT_LIMIT);
    return {
        filePath: ctx.filePath,
        line: ctx.line,
        repoPath: ctx.repoPath,
        blame: meta,
        commits,
        branch: localGit.getCurrentBranch(ctx.repoPath),
        remoteUrl: localGit.getRemoteUrl(ctx.repoPath),
    };
}

/** 파일 단위 추적 시 백엔드로 보낼 커밋 상한 — 외부 API 조회량을 제어한다. */
const TRACE_COMMIT_LIMIT = 20;

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

interface FileChangeSignal { added: number; removed: number; symbols: string; }

/**
 * 타임라인 하이브리드 요약용 — 이 파일에 대한 커밋별 '실제 코드 변경' 신호를 git diff 에서 추출한다.
 *
 * `git log -p --unified=0` 한 번으로 전체 이력의 패치를 받아, 커밋마다
 *   · +/- 줄 수(diffstat) 와
 *   · @@ 헌크 헤더 뒤에 git 이 달아주는 '함수/섹션 컨텍스트'(바뀐 심볼)
 * 만 추려 들고, 패치 본문은 버린다(토큰·payload 최소화).
 * %x01 은 git 이 출력하는 레코드 구분 바이트(0x01) — 패치 내용과 안 겹치는 안전한 경계.
 *
 * 👤 담당: 개발자 B
 */
/** `git log -p` 출력을 커밋별 변경 신호(+/-줄 수 + 헌크 함수명)로 파싱한다. */
function parseFileChangeSignals(out: string): Map<string, FileChangeSignal> {
    const map = new Map<string, FileChangeSignal>();
    for (const block of out.split('\x01')) {
        const nl = block.indexOf('\n');
        const hash = (nl < 0 ? block : block.slice(0, nl)).trim();
        if (!hash) { continue; }
        const body = nl < 0 ? '' : block.slice(nl + 1);
        let added = 0, removed = 0;
        const symbols = new Set<string>();
        for (const line of body.split('\n')) {
            if (line.startsWith('@@')) {
                // "@@ -a,b +c,d @@ <context>" — <context> 는 보통 이 헌크가 속한 함수/섹션명.
                const ctx = line.slice(line.indexOf('@@', 2) + 2).trim();
                if (ctx) { symbols.add(ctx); }
            } else if (line.startsWith('+') && !line.startsWith('+++')) {
                added++;
            } else if (line.startsWith('-') && !line.startsWith('---')) {
                removed++;
            }
        }
        // 심볼은 토큰을 아끼려고 최대 4개까지만.
        map.set(hash, { added, removed, symbols: [...symbols].slice(0, 4).join(', ') });
    }
    return map;
}

const GIT_DIFF_ARGS = (filePath: string) =>
    ['log', '--follow', '-p', '--unified=0', '--format=%x01%H', '--', filePath];

function collectFileChangeSignals(repoPath: string, filePath: string): Map<string, FileChangeSignal> {
    try {
        const out = execSync(
            `git log --follow -p --unified=0 --format="%x01%H" -- "${filePath}"`,
            { cwd: repoPath, timeout: 20_000, maxBuffer: 64 * 1024 * 1024 },
        ).toString();
        return parseFileChangeSignals(out);
    } catch {
        // diff 수집 실패(대용량/타임아웃 등)는 치명적이지 않다 — 메시지만으로 폴백한다.
        return new Map();
    }
}

const execFileAsync = promisify(execFile);

/** 확장 호스트를 막지 않는 비동기 버전 — 블레임 라인 타이틀처럼 사후 보강에 쓴다. */
async function collectFileChangeSignalsAsync(repoPath: string, filePath: string): Promise<Map<string, FileChangeSignal>> {
    try {
        const { stdout } = await execFileAsync('git', GIT_DIFF_ARGS(filePath), {
            cwd: repoPath, timeout: 20_000, maxBuffer: 64 * 1024 * 1024,
        });
        return parseFileChangeSignals(stdout.toString());
    } catch {
        return new Map();
    }
}

/**
 * 라인 수정 이력의 행 타이틀을 'AI 가 다듬은 한 줄'로 진행형 교체한다.
 * 목록은 이미 원본 커밋 메시지로 즉시 렌더된 상태 — 여기서는 사후에 비동기로 다듬어 갈아끼운다.
 * 실제 변경(diff) 신호를 함께 실어, 모호한 메시지도 코드 변경 기준으로 다듬도록 grounding 한다.
 */
async function applyLineTitles(
    lineHistory: { hash: string; subject: string }[] | undefined,
    ctx: { filePath: string; repoPath: string },
) {
    if (!sidebar || !lineHistory || lineHistory.length === 0) { return; }
    let titles: Record<string, string> = {};
    try {
        const signals = await collectFileChangeSignalsAsync(ctx.repoPath, ctx.filePath);
        const commits = lineHistory.map(h => {
            const s = signals.get(h.hash);
            return s
                ? { hash: h.hash, subject: h.subject || '', linesAdded: s.added, linesRemoved: s.removed, changedSymbols: s.symbols }
                : { hash: h.hash, subject: h.subject || '' };
        });
        titles = await fetchLineTitles({ filePath: ctx.filePath, repoPath: ctx.repoPath, commits });
    } catch {
        // 실패 → 원본 메시지를 타이틀로 실어, 웹뷰가 스켈레톤을 풀고 원본으로 즉시 드러내게 한다.
        titles = Object.fromEntries(lineHistory.map(h => [h.hash, h.subject || '']));
    }
    // 성공/실패 무관하게 항상 호출 — 모든 행의 스켈레톤이 한 번에 해제되어야 한다.
    sidebar.setHistoryTitles(titles);
}

/** git log(메타) + git diff(변경 신호)를 합쳐, 커밋마다 실제 변경을 덧붙인 목록을 만든다. */
function collectCommitsWithChanges(repoPath: string, filePath: string): CommitInput[] {
    const commits = collectGitLog(repoPath, filePath);
    const signals = collectFileChangeSignals(repoPath, filePath);
    return commits.map(c => {
        const s = signals.get(c.hash);
        return s
            ? { ...c, linesAdded: s.added, linesRemoved: s.removed, changedSymbols: s.symbols }
            : c;
    });
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

/**
 * 이슈 상세 'AI 질문' — 웹뷰가 보낸 (이슈 컨텍스트 + 대화 히스토리)로 백엔드 챗봇을
 * 스트리밍 호출하고, 토큰 델타를 사이드바(웹뷰)로 그대로 중계한다.
 * 첨부 다운로드는 백엔드가 서버 토큰으로 수행하므로 여기서 별도 인증을 싣지 않는다.
 */
async function handleIssueChat(payload: { issue: any; messages: { role: string; content: string }[] }) {
    if (!sidebar) { return; }
    const issue = payload.issue || {};
    // 연관 커밋 diff 를 백엔드가 호스팅 API 로 가져오려면 remote URL 이 필요하다(로컬 git 에서 추출).
    const repoPath = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath ?? '';
    const remoteUrl = repoPath ? localGit.getRemoteUrl(repoPath) : null;
    const req: IssueChatRequest = {
        issueNumber: issue.issueNumber ?? null,
        title: issue.title ?? '',
        body: issue.body ?? '',
        state: issue.state ?? '',
        labels: issue.labels ?? [],
        assignee: issue.assignee ?? '',
        url: issue.url ?? '',
        attachments: issue.attachments ?? [],
        comments: issue.comments ?? [],
        linkedCommits: issue.linkedCommits ?? [],
        repoPath,
        remoteUrl,
        messages: payload.messages as IssueChatMessage[],
    };
    try {
        await streamIssueChat(req, {
            onDelta: (delta) => sidebar?.postIssueChat({ kind: 'delta', text: delta }),
            onDone: () => sidebar?.postIssueChat({ kind: 'done' }),
            onError: (message) => sidebar?.postIssueChat({ kind: 'error', text: message }),
        });
    } catch (err) {
        sidebar?.postIssueChat({ kind: 'error', text: (err as Error).message });
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
