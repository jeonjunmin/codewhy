import * as vscode from 'vscode';
import { EditorContext } from '../../shared/editor';
import { log } from '../../shared/log';
import { BlameResult, TimelineResult, TraceResult } from '../../shared/types';
import { BlameMeta } from './api';

/**
 * CodeWhy 통합 사이드바 — 하나의 WebviewView 안에서 탭으로 세 기능을 전환한다.
 *
 *   [블레임] [타임라인] [이슈]   ← 공통 탭바 (맨 위, 컨테이너 타이틀 'CodeWhy' 아래)
 *
 *   · 블레임  : 라인 단위 변경 사유 + 메타 + 라인 수정 이력  (개발자 A)
 *   · 타임라인: 파일 변경 역사 AI 요약 + 마일스톤(스트리밍)   (개발자 B)
 *   · 이슈    : 현재 라인과 연관된 GitHub Issue 역추적 목록    (개발자 C)
 *
 * 세 기능 모두 각자 webview 를 만들지 않고, 이 provider 의 set*() 메서드로
 * postMessage 데이터만 밀어넣는다. 탭 클릭 시에는 onSwitchTab 으로 확장에 알려
 * 현재 커서 라인 기준 분석을 자동 실행시킨다.
 *
 * 라이프사이클:
 *   - WebviewView 는 활성화될 때 한 번 resolve 된다.
 *   - 상태 변경마다 postMessage 만 보낸다 — HTML 은 다시 안 그린다.
 */

export const VIEW_ID = 'codewhy.contextBlame';

type TimelineState =
    // summary: summaryDone 수신 후 확정된 상단 요약(있으면 재표시 시 상단을 '완료'로 복원).
    | { kind: 'streaming'; fileName: string; text: string; summary?: string }
    | { kind: 'result'; fileName: string; result: TimelineResult }
    | { kind: 'empty'; message?: string };

// 블레임 탭 스트리밍 상태 — 타임라인 TimelineState 와 동일한 구조.
// 'streaming' 은 meta 프레임 수신 후 설명 토큰을 받는 중, 'result' 는 done 수신 후 확정.
type BlameStreamState =
    | { kind: 'streaming'; ctx: EditorContext; meta: BlameMeta; text: string }
    | { kind: 'result'; ctx: EditorContext; result: BlameResult };

type IssueState =
    | { kind: 'loading' }
    | { kind: 'result'; line: number; fileName: string; result: TraceResult }
    | { kind: 'empty'; message?: string }
    // 커밋 스코프(라인 수정 이력의 '이슈 N' 배지) — 파일 검색과 달리 한 커밋이 참조한 이슈만.
    | { kind: 'commitLoading'; hash: string; subject: string }
    | { kind: 'commitResult'; hash: string; subject: string; result: TraceResult; empty?: string };

export class ContextBlameSidebarProvider implements vscode.WebviewViewProvider {
    private view?: vscode.WebviewView;
    private last?: { ctx: EditorContext; result: BlameResult; pinned: boolean };
    private lastBlameStream?: BlameStreamState;
    // 커밋 해시 → AI 가 다듬은 라인 이력 타이틀. 웹뷰 재생성 시 스켈레톤을 즉시 메우기 위해 보관.
    private lastLineTitles: Record<string, string> = {};
    private lastTimeline?: TimelineState;
    private lastIssue?: IssueState;
    private activeTab: 'blame' | 'timeline' | 'issue' = 'blame';
    /**
     * 웹뷰 스크립트가 message 리스너를 등록하고 'ready' 를 보내올 때까지 false.
     * html 주입 직후 보낸 postMessage 는 리스너가 붙기 전이라 유실되므로,
     * ready 이전에는 렌더를 쏘지 않고 this.last 에만 담아 뒀다가 ready 수신 시 flush 한다.
     */
    private ready = false;

    constructor(
        private readonly extensionUri: vscode.Uri,
        private readonly handlers: {
            onOpenCommit: (commitHash: string, repoPath: string) => void;
            onSwitchTab: (tab: string) => void;
            onOpenIssue: (url: string) => void;
            // '라인 수정 이력'의 '이슈 N' 배지 클릭 — 이슈 탭에서 그 커밋의 연관 이슈를 연다.
            onOpenCommitIssues: (hash: string, filePath: string, repoPath: string) => void;
            // 라인 수정 이력 항목 펼침 — 그 커밋의 변경 사유를 지연 생성한다.
            onExpandHistory: (hash: string, filePath: string, repoPath: string) => void;
            // 이슈 상세의 'AI 질문' — 현재 이슈를 컨텍스트로 멀티턴 답변을 스트리밍한다.
            onIssueChat: (payload: { issue: any; messages: { role: string; content: string }[] }) => void;
        },
    ) {}

    resolveWebviewView(view: vscode.WebviewView) {
        log('sidebar', 'resolveWebviewView 호출됨 — 패널이 펼쳐짐');
        this.view = view;
        this.ready = false;  // 새 웹뷰 인스턴스 — 스크립트가 'ready' 를 보낼 때까지 대기
        view.webview.options = { enableScripts: true, localResourceRoots: [this.extensionUri] };

        // ⚠️ 메시지 리스너를 html 주입 '전에' 등록한다.
        // html 을 넣는 순간 웹뷰 스크립트가 실행되며 곧장 'ready' 를 보내는데,
        // 리스너가 그 뒤에 붙으면(특히 리로드로 웹뷰 프로세스가 warm 할 때) ready 를 놓쳐
        // flushPending 이 영영 안 돌고 패널이 빈 상태로 멈춘다.
        view.webview.onDidReceiveMessage(msg => this.handleMessage(msg));
        view.webview.html = this.renderHtml();

        // 웹뷰가 사라지면(탭 닫힘 등) 다음 resolve 가 다시 핸드셰이크하도록 상태 초기화
        view.onDidDispose(() => {
            this.view = undefined;
            this.ready = false;
        });

        // ⚠️ 여기서 곧바로 postMessage 하지 않는다 — 웹뷰 스크립트의 message 리스너가
        //    아직 등록되기 전이라 유실된다. 실제 렌더는 'ready' 수신 시 flushPending() 에서.
    }

    /** 웹뷰 스크립트가 'ready' 를 보내오면 각 탭의 마지막 상태를 한 번 그린다. */
    private flushPending() {
        // 블레임 — 스트리밍 중이면 그 상태를, 아니면 마지막 확정 결과를 복원.
        if (this.lastBlameStream?.kind === 'streaming') {
            this.postBlameStream(this.lastBlameStream);
        } else if (this.last) {
            this.postRender(this.last.ctx, this.last.result, this.last.pinned);
        } else {
            this.postEmpty();
        }
        // 타임라인 / 이슈 — 마지막 상태가 있으면 복원
        if (this.lastTimeline) { this.postTimeline(this.lastTimeline); }
        if (this.lastIssue) { this.postIssue(this.lastIssue); }
        // 활성 탭 복원 (블레임이 아니면 전환)
        if (this.activeTab !== 'blame') {
            this.view?.webview.postMessage({ type: 'activateTab', payload: { tab: this.activeTab } });
        }
    }

    /** Context Blame 분석이 끝났을 때 view.ts 에서 호출. */
    setBlame(ctx: EditorContext, result: BlameResult, pinned: boolean) {
        this.last = { ctx, result, pinned };
        log('sidebar', 'setBlame', { hasView: !!this.view, ready: this.ready });
        if (!this.view) {
            // 사이드바가 아직 안 열려 있으면 강제로 표시한다 — 첫 분석 시 자연스럽게 펼쳐짐
            log('sidebar', `view 없음 → ${VIEW_ID}.focus 실행`);
            vscode.commands.executeCommand(`${VIEW_ID}.focus`).then(
                () => log('sidebar', 'focus 명령 완료'),
                (e) => log('sidebar', 'focus 명령 실패', String(e)),
            );
            return;
        }
        this.view.show?.(true);
        if (!this.ready) {
            // 웹뷰 스크립트가 아직 ready 전 — 지금 postMessage 하면 유실될 수 있으니
            // this.last 에만 담아 두고, ready 수신 시 flushPending() 이 그린다.
            log('sidebar', 'view 있으나 아직 ready 전 — flushPending 에 위임');
            return;
        }
        // 확정 렌더가 진행 중이던 스트림을 대체한다(커서 이동/캐시 적중 등).
        this.lastBlameStream = undefined;
        this.postRender(ctx, result, pinned);
    }

    // ─── 블레임 탭 스트리밍 (개발자 A) ─────────────────────────────────────
    // 타임라인 timelineStreaming/Delta/Result 3단 패턴을 그대로 미러링한다.

    /** meta 프레임 수신 — 메타/라인 이력을 즉시 그리고 콜아웃을 캐럿과 함께 연다. */
    blameStreaming(ctx: EditorContext, meta: BlameMeta) {
        this.activeTab = 'blame';
        this.lastBlameStream = { kind: 'streaming', ctx, meta, text: '' };
        if (!this.view) {
            // 사이드바가 아직 안 열려 있으면 강제로 표시 — ready 수신 시 flushPending 이 그린다.
            // 그 사이 도착한 delta 는 lastBlameStream.text 에 누적돼 flush 때 함께 렌더된다.
            vscode.commands.executeCommand(`${VIEW_ID}.focus`);
            return;
        }
        this.view.show?.(true);
        if (!this.ready) { return; }  // ready 전 — flushPending 에 위임
        this.postBlameStream(this.lastBlameStream);
    }
    /** 설명 토큰 한 조각 — 콜아웃에 이어 붙인다. */
    blameDelta(delta: string) {
        if (this.lastBlameStream?.kind === 'streaming') { this.lastBlameStream.text += delta; }
        this.view?.webview.postMessage({ type: 'blDelta', payload: { delta } });
    }
    /** done 프레임 수신 — 캐럿을 제거하고 출처/PR 등 나머지 필드를 확정한다. */
    blameResult(ctx: EditorContext, result: BlameResult) {
        this.lastBlameStream = { kind: 'result', ctx, result };
        // 확정 결과는 this.last 에도 담아, 웹뷰 재로드 시 postRender 로 깔끔히 복원되게 한다.
        this.last = { ctx, result, pinned: false };
        this.postBlameStream(this.lastBlameStream);
    }

    /** 펼친 라인 이력 항목에 그 커밋의 변경 사유를 주입한다(지연 로드 응답). */
    setHistoryReason(hash: string, reason: string) {
        this.view?.webview.postMessage({ type: 'historyReason', payload: { hash, reason } });
    }

    /** 라인 이력 행 타이틀을 AI 가 다듬은 것으로 드러낸다({hash: 타이틀}).
     * 커밋 해시별로 보관해, 웹뷰가 재생성되며 이력을 다시 그릴 때(탭 전환·재방문) 스켈레톤이
     * 영구히 남지 않도록 같은 타이틀을 다시 실어 보낸다(postLineTitles). */
    setHistoryTitles(titles: Record<string, string>) {
        this.lastLineTitles = { ...this.lastLineTitles, ...titles };
        this.view?.webview.postMessage({ type: 'historyTitles', payload: { titles } });
    }

    /** 라인 이력을 (재)렌더한 직후 호출 — 보관해 둔 타이틀로 스켈레톤을 즉시 드러낸다. */
    private postLineTitles() {
        if (!this.ready || Object.keys(this.lastLineTitles).length === 0) { return; }
        this.view?.webview.postMessage({ type: 'historyTitles', payload: { titles: this.lastLineTitles } });
    }

    /** 이슈 챗봇 스트림 프레임을 웹뷰로 중계한다(view.ts onIssueChat 에서 호출). */
    postIssueChat(payload: { kind: 'delta' | 'done' | 'error'; text?: string }) {
        this.view?.webview.postMessage({ type: 'issueChat', payload });
    }

    private postBlameStream(s: BlameStreamState) {
        const wv = this.view?.webview;
        if (!wv || !this.ready) { return; }
        if (s.kind === 'streaming') {
            const { ctx, meta } = s;
            const fileName = ctx.filePath.split(/[\\/]/).pop() ?? ctx.filePath;
            wv.postMessage({
                type: 'blStreaming',
                payload: {
                    fileName,
                    line: ctx.line,
                    author: meta.author,
                    team: meta.team,
                    commitShort: (meta.commitHash || '').slice(0, 7),
                    ticket: meta.ticket,
                    dateShort: formatDisplayDate(meta.date),
                    dateFull: formatISODate(meta.date),
                    relative: formatRelativeKo(meta.date),
                    changeStats: meta.changeStats,
                    lineHistory: meta.lineHistory ?? [],
                    lineIssues: meta.lineIssues ?? [],
                    text: s.text,
                },
            });
            this.postLineTitles();   // 재방문 시 스켈레톤을 보관된 타이틀로 즉시 메운다.
        } else {
            const r = s.result;
            wv.postMessage({
                type: 'blResult',
                payload: {
                    explanation: (r.explanation ?? '').trim(),
                    headline: r.headline ?? null,
                    sourceRef: r.sourceRef ?? r.specRef ?? null,
                    team: r.team ?? null,
                    ticket: r.ticket ?? null,
                    changeStats: r.changeStats,
                    prInfo: r.prInfo,
                },
            });
        }
    }


    // ─── 타임라인 탭 (개발자 B) ───────────────────────────────────────────
    /** 확장 측에서 특정 탭을 띄우고 싶을 때(패널 강제 표시 포함). */
    activateTab(tab: 'blame' | 'timeline' | 'issue') {
        this.activeTab = tab;
        if (!this.view) {
            vscode.commands.executeCommand(`${VIEW_ID}.focus`);
            return;
        }
        this.view.show?.(true);
        if (this.ready) {
            this.view.webview.postMessage({ type: 'activateTab', payload: { tab } });
        }
    }

    timelineStreaming(fileName: string) {
        this.lastTimeline = { kind: 'streaming', fileName, text: '' };
        this.postTimeline(this.lastTimeline);
    }
    timelineDelta(delta: string) {
        if (this.lastTimeline?.kind === 'streaming') { this.lastTimeline.text += delta; }
        this.view?.webview.postMessage({ type: 'tlDelta', payload: { delta } });
    }
    // 상단 요약 조기 확정 — 마일스톤 토큰이 마저 오는 동안 상단을 '완료'로 보여준다.
    timelineSummaryDone(fileName: string, summary: string) {
        if (this.lastTimeline?.kind === 'streaming') { this.lastTimeline.summary = summary; }
        this.view?.webview.postMessage({ type: 'tlSummaryDone', payload: { fileName, summary } });
    }
    timelineResult(fileName: string, result: TimelineResult) {
        this.lastTimeline = { kind: 'result', fileName, result };
        this.postTimeline(this.lastTimeline);
    }
    timelineEmpty(message?: string) {
        this.lastTimeline = { kind: 'empty', message };
        this.postTimeline(this.lastTimeline);
    }

    private postTimeline(s: TimelineState) {
        const wv = this.view?.webview;
        if (!wv || !this.ready) { return; }
        if (s.kind === 'streaming') {
            wv.postMessage({ type: 'tlStreaming', payload: { fileName: s.fileName, text: s.text } });
            // 이미 상단 요약이 확정된 뒤 뷰가 다시 열렸다면 '완료' 상태로 복원한다.
            if (s.summary) { wv.postMessage({ type: 'tlSummaryDone', payload: { fileName: s.fileName, summary: s.summary } }); }
        } else if (s.kind === 'result') {
            wv.postMessage({ type: 'tlResult', payload: { fileName: s.fileName, summary: s.result.summary, milestones: s.result.milestones } });
        } else {
            wv.postMessage({ type: 'tlEmpty', payload: { message: s.message } });
        }
    }

    // ─── 이슈 탭 (개발자 C) ───────────────────────────────────────────────
    issueLoading() {
        this.lastIssue = { kind: 'loading' };
        this.postIssue(this.lastIssue);
    }
    issueResult(line: number, fileName: string, result: TraceResult) {
        this.lastIssue = { kind: 'result', line, fileName, result };
        this.postIssue(this.lastIssue);
    }
    issueEmpty(message?: string) {
        this.lastIssue = { kind: 'empty', message };
        this.postIssue(this.lastIssue);
    }

    // ── 커밋 스코프(라인 수정 이력의 '이슈 N' 배지) ─────────────────────────
    /** 그 커밋의 이슈를 역추적하는 동안 — 배너+스피너를 보여준다. */
    issueCommitLoading(hash: string, subject: string) {
        this.lastIssue = { kind: 'commitLoading', hash, subject };
        this.postIssue(this.lastIssue);
    }
    /** 역추적 결과 — 그 커밋이 참조한 이슈 목록을 커밋 배너와 함께 그린다. */
    issueCommitResult(hash: string, subject: string, result: TraceResult) {
        this.lastIssue = { kind: 'commitResult', hash, subject, result };
        this.postIssue(this.lastIssue);
    }
    /** 결과 없음/실패 — 배너는 유지한 채 목록 자리에 안내 문구만. */
    issueCommitEmpty(hash: string, subject: string, message?: string) {
        this.lastIssue = { kind: 'commitResult', hash, subject, result: { documents: [] }, empty: message };
        this.postIssue(this.lastIssue);
    }

    private postIssue(s: IssueState) {
        const wv = this.view?.webview;
        if (!wv || !this.ready) { return; }
        if (s.kind === 'loading') {
            wv.postMessage({ type: 'isLoading' });
        } else if (s.kind === 'result') {
            wv.postMessage({ type: 'isResult', payload: { line: s.line, fileName: s.fileName, documents: s.result.documents } });
        } else if (s.kind === 'commitLoading') {
            wv.postMessage({ type: 'isCommitLoading', payload: { hash: s.hash, subject: s.subject } });
        } else if (s.kind === 'commitResult') {
            wv.postMessage({ type: 'isCommitResult', payload: { hash: s.hash, subject: s.subject, documents: s.result.documents, empty: s.empty } });
        } else {
            wv.postMessage({ type: 'isEmpty', payload: { message: s.message } });
        }
    }

    // ─── 메시지 라우팅 ────────────────────────────────────────────────────
    private handleMessage(msg: { type: string; payload?: any }) {
        // 'ready' 는 분석 결과(this.last) 유무와 무관하게 항상 먼저 처리한다.
        if (msg.type === 'ready') {
            log('sidebar', 'webview ready 수신 → flushPending', { hasLast: !!this.last });
            this.ready = true;
            this.flushPending();
            return;
        }
        if (msg.type === 'webview-error') {
            log('sidebar', '⚠️ webview 스크립트 오류', msg.payload);
            return;
        }
        // 탭 전환은 분석 결과(this.last) 없이도 동작해야 한다 — 탭바는 항상 떠 있다.
        if (msg.type === 'switchTab') {
            const tab = msg.payload?.tab;
            if (tab === 'blame' || tab === 'timeline' || tab === 'issue') {
                this.activeTab = tab;
                this.handlers.onSwitchTab(tab);
            }
            return;
        }
        // 이슈 항목 클릭도 블레임 결과와 무관하게 동작해야 한다.
        if (msg.type === 'openIssue') {
            if (typeof msg.payload?.url === 'string' && msg.payload.url) {
                this.handlers.onOpenIssue(msg.payload.url);
            }
            return;
        }
        // 타임라인 파일명 옆 휴지통 — 기존 명령을 재사용해 활성 파일의 타임라인 캐시를 비운다.
        if (msg.type === 'clearTimelineCache') {
            vscode.commands.executeCommand('codewhy.timeline.clearCache');
            return;
        }
        // 돋보기 파일명 옆 휴지통 — 활성 파일의 돋보기 설명 캐시를 비운다(시연 재분석용).
        if (msg.type === 'clearBlameCache') {
            vscode.commands.executeCommand('codewhy.blame.clearCache');
            return;
        }
        // 이슈 상세의 'AI 질문' — 블레임 결과(this.last) 유무와 무관하게 동작해야 한다.
        if (msg.type === 'issueChatAsk') {
            if (msg.payload?.issue && Array.isArray(msg.payload?.messages)) {
                this.handlers.onIssueChat(msg.payload);
            }
            return;
        }
        // '라인 수정 이력'의 '이슈 N' 배지 클릭 — 현재 블레임 파일/레포 맥락으로 그 커밋의 이슈를 연다.
        if (msg.type === 'openCommitIssues') {
            const hash = msg.payload?.hash;
            const ctx = this.last?.ctx ?? this.lastBlameStream?.ctx;
            if (typeof hash === 'string' && hash && ctx) {
                this.handlers.onOpenCommitIssues(hash, ctx.filePath, ctx.repoPath);
            }
            return;
        }
        // 라인 수정 이력 항목 펼침 — 현재 표시 중인 블레임 파일/레포 맥락으로 그 커밋 사유를 요청.
        if (msg.type === 'expandHistory') {
            const hash = msg.payload?.hash;
            const ctx = this.last?.ctx ?? this.lastBlameStream?.ctx;
            if (typeof hash === 'string' && hash && ctx) {
                this.handlers.onExpandHistory(hash, ctx.filePath, ctx.repoPath);
            }
            return;
        }
        if (!this.last) { return; }
        const { ctx, result } = this.last;
        switch (msg.type) {
            case 'openCommit':
                this.handlers.onOpenCommit(result.commitHash, ctx.repoPath);
                break;
        }
    }

    // ─── postMessage 헬퍼 ─────────────────────────────────────────────────
    private postEmpty() {
        this.view?.webview.postMessage({ type: 'empty' });
    }

    private postRender(ctx: EditorContext, r: BlameResult, pinned: boolean) {
        // commitHash 가 비면 분석할 커밋 이력이 없는 경우(미커밋 라인 등) — 백엔드의
        // uncommitted_response. 메타/관련변경/CTA 는 채울 게 없으므로 안내 문구만 표시한다.
        if (!r.commitHash) {
            log('sidebar', 'commitHash 없음 → info 안내 상태로 렌더');
            this.view?.webview.postMessage({
                type: 'info',
                payload: { message: r.explanation || '이 라인의 변경 이력을 찾을 수 없습니다.' },
            });
            return;
        }

        const fileName = ctx.filePath.split(/[\\/]/).pop() ?? ctx.filePath;
        const payload = {
            fileName,
            line: ctx.line,
            explanation: (r.explanation ?? '').trim(),
            headline: r.headline ?? null,
            author: r.author,
            team: r.team,
            commitShort: (r.commitHash || '').slice(0, 7),
            ticket: r.ticket,
            dateShort: formatDisplayDate(r.date),   // "3월 15일" — 콜아웃 문장용
            dateFull: formatISODate(r.date),         // "2026-03-15" — 메타 표용
            relative: formatRelativeKo(r.date),
            changeStats: r.changeStats,
            prInfo: r.prInfo,
            sourceRef: r.sourceRef ?? r.specRef ?? null,
            lineHistory: r.lineHistory ?? [],
            lineIssues: r.lineIssues ?? [],
            pinned,
        };
        this.view?.webview.postMessage({ type: 'render', payload });
        this.postLineTitles();   // 재방문 시 스켈레톤을 보관된 타이틀로 즉시 메운다.
    }

    // ─── 한 번만 그리는 HTML 빨대 컵 ──────────────────────────────────────
    private renderHtml(): string {
        const nonce = randomNonce();
        const csp = [
            `default-src 'none'`,
            `style-src ${this.view?.webview.cspSource ?? "'self'"} 'unsafe-inline'`,
            `script-src 'nonce-${nonce}'`,
            // 이슈 첨부/본문 이미지는 GitHub(githubusercontent 리다이렉트 포함) 등 외부 https 호스트에 있으므로 허용.
            `img-src ${this.view?.webview.cspSource ?? "'self'"} https: data:`,
            `font-src ${this.view?.webview.cspSource ?? "'self'"}`,
        ].join('; ');

        return /* html */ `<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8" />
<meta http-equiv="Content-Security-Policy" content="${csp}" />
<style>
    /* ── 디자인 토큰 ─────────────────────────────────────────────── */
    :root {
        --bg: transparent;
        --fg: #FAFAFA;
        --fg-dim: #A1A1AA;
        --fg-mute: #71717A;
        --line: #27272A;
        --line-soft: rgba(167,139,250,0.30);
        --surface: #18181B;
        --surface-2: #0F0F12;
        --code-bg: #3F3F46;
        --code-fg: #FDE047;
        --accent-cyan: #67E8F9;
        --accent-violet: #A78BFA;
        --grad: linear-gradient(135deg, #0E7490 0%, #6D28D9 100%);
        --callout-bg: linear-gradient(180deg, rgba(109,40,217,0.18) 0%, rgba(14,116,144,0.10) 100%);
    }
    *, *::before, *::after { box-sizing: border-box; }
    html, body { margin: 0; padding: 0; background: var(--bg); color: var(--fg); }
    body {
        font-family: var(--vscode-font-family);
        font-size: 12.5px;
        line-height: 1.5;
    }
    code, .mono { font-family: var(--vscode-editor-font-family, ui-monospace, monospace); }

    /* ── 공통 탭바 (블레임 / 타임라인 / 이슈) ─────────────────────── */
    .tabs {
        display: flex; gap: 4px;
        padding: 8px 10px;
        border-bottom: 1px solid var(--line);
    }
    .tab {
        flex: 1;
        display: inline-flex; align-items: center; justify-content: center; gap: 5px;
        padding: 6px 8px;
        background: transparent; border: none; border-radius: 7px;
        color: var(--fg-mute); font-size: 12px; font-weight: 500;
        font-family: inherit; cursor: pointer;
        transition: background 0.12s, color 0.12s;
    }
    .tab:hover { background: var(--line); color: var(--fg-dim); }
    .tab.active {
        background: var(--surface);
        color: var(--fg);
        box-shadow: inset 0 0 0 1px var(--line);
    }
    .tab__ico { display: inline-flex; opacity: 0.85; }

    /* ── 본문 컨테이너 ───────────────────────────────────────────── */
    .body { padding: 14px; display: flex; flex-direction: column; gap: 14px; }

    /* ── 빈 상태 ─────────────────────────────────────────────────── */
    .empty {
        padding: 28px 18px;
        color: var(--fg-mute);
        font-size: 12px; line-height: 1.6;
        text-align: center;
    }
    .empty .kbd {
        display: inline-block;
        background: var(--line); color: var(--fg-dim);
        padding: 1px 6px; border-radius: 4px; font-size: 11px;
    }

    /* ── 환영(온보딩) 화면 — 설치 직후 첫 화면 ─────────────────────── */
    .hero {
        display: flex; flex-direction: column; align-items: center; text-align: center;
        padding: 40px 24px 28px;
        gap: 14px;
    }
    .hero__badge {
        width: 64px; height: 64px; border-radius: 18px;
        display: inline-flex; align-items: center; justify-content: center;
        background: linear-gradient(135deg, rgba(103,232,249,0.18) 0%, rgba(167,139,250,0.22) 100%);
        border: 1px solid var(--line-soft);
        box-shadow: 0 0 28px rgba(167,139,250,0.30);
        color: var(--accent-violet);
        margin-bottom: 4px;
    }
    .hero__title {
        margin: 0; font-size: 18px; font-weight: 700; color: var(--fg);
        letter-spacing: -0.01em;
    }
    .hero__desc {
        margin: 0; max-width: 320px;
        color: var(--fg-dim); font-size: 13px; line-height: 1.65;
    }
    .hero__desc strong { color: var(--fg); font-weight: 700; }
    .hero__cta {
        width: 100%; max-width: 340px; margin-top: 6px;
        display: inline-flex; align-items: center; justify-content: center; gap: 8px;
        padding: 12px 16px; border: none; border-radius: 12px;
        background: var(--grad); color: #fff;
        font-family: inherit; font-size: 14px; font-weight: 700; cursor: pointer;
        box-shadow: 0 6px 20px rgba(109,40,217,0.35);
        transition: filter 0.12s, transform 0.06s;
    }
    .hero__cta:hover { filter: brightness(1.08); }
    .hero__cta:active { transform: translateY(1px); }
    .hero__cta span { display: inline-flex; }
    .hero__status {
        display: inline-flex; align-items: center; gap: 6px;
        margin-top: 4px;
        color: #4ADE80; font-size: 11.5px;
    }
    .hero__status span { display: inline-flex; }

    /* ── 안내 상태: 커밋 이력 없음(미커밋 라인 등) ────────────────── */
    .info {
        margin: 14px;
        padding: 14px 16px;
        display: flex; gap: 10px; align-items: flex-start;
        background: var(--surface);
        border: 1px solid var(--line);
        border-radius: 10px;
    }
    .info__icon { color: var(--accent-violet); flex-shrink: 0; margin-top: 1px; }
    .info__text { color: var(--fg-dim); font-size: 12.5px; line-height: 1.6; }

    /* ── 콜아웃: 이 라인이 추가된 이유 ───────────────────────────── */
    .callout {
        background: var(--callout-bg);
        border: 1px solid var(--line-soft);
        border-radius: 14px;
        padding: 14px 16px;
    }
    /* 작성자 행 — 아바타 + 이름 + 날짜 */
    .callout__author { display: flex; align-items: center; gap: 9px; margin-bottom: 12px; }
    .callout__avatar {
        flex-shrink: 0; width: 30px; height: 30px; border-radius: 50%;
        display: inline-flex; align-items: center; justify-content: center;
        background: var(--grad); color: #fff;
        font-size: 11px; font-weight: 700; letter-spacing: 0.3px;
    }
    .callout__who { display: flex; flex-direction: column; line-height: 1.3; min-width: 0; }
    .callout__name { color: var(--fg); font-size: 12.5px; font-weight: 600; }
    .callout__when { color: var(--fg-mute); font-size: 11px; }
    /* 핵심 한 줄 — 굵게, 가장 먼저 눈에 들어오는 결론 */
    .callout__lead { color: var(--fg); font-size: 13.5px; line-height: 1.62; font-weight: 600; }
    /* 자세한 배경 */
    .callout__detail { margin-top: 13px; }
    .callout__detail-label { color: var(--fg-mute); font-size: 11px; font-weight: 500; margin-bottom: 5px; }
    .callout__detail-body { color: var(--fg-dim); font-size: 12.5px; line-height: 1.65; }
    /* 핵심 구절 강조(마커) — 백엔드 **강조** 구절을 옅은 보라 형광펜으로.
       작성자 이름(.callout__name)도 <strong> 이므로, 본문 영역으로만 범위를 좁힌다. */
    .callout__lead strong, .callout__lead b,
    .callout__detail-body strong, .callout__detail-body b {
        font-weight: 700; color: var(--fg);
        background: rgba(167,139,250,0.16);
        border-radius: 3px; padding: 0 3px;
        -webkit-box-decoration-break: clone; box-decoration-break: clone;
    }
    .callout__more {
        display: inline-flex; align-items: center; gap: 6px;
        margin-top: 13px; padding: 0; background: none; border: none;
        color: var(--fg-mute); font-size: 11.5px; cursor: pointer; font-family: inherit;
    }
    .callout__more::before {
        content: ''; width: 0; height: 0;
        border-left: 4px solid transparent; border-right: 4px solid transparent;
        border-top: 5px solid currentColor;
    }
    /* 펼친 상태(접기) — 셰브론을 위로 뒤집는다 */
    .callout__more.expanded::before {
        border-top: none; border-bottom: 5px solid currentColor;
    }
    .callout__more:hover { color: var(--fg-dim); }
    /* 출처/팀/PR 칩 — 메타 표에 묻혀 있던 핵심을 콜아웃 옆에서 한눈에. */
    .callout__chips { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 12px; }
    .ca-chip {
        display: inline-flex; align-items: center; gap: 4px;
        font-size: 11px; padding: 2px 8px; border-radius: 6px;
        background: var(--surface); border: 1px solid var(--line); color: var(--fg-dim);
    }
    .ca-chip svg { opacity: 0.8; }
    .ca-chip--src { color: var(--accent-violet); border-color: var(--line-soft); }
    .callout code {
        background: var(--code-bg); color: var(--code-fg);
        padding: 1px 5px; border-radius: 4px; font-size: 11.5px;
    }

    /* ── 파일 헤더(상단) ─────────────────────────────────────────── */
    .crumb {
        display: flex; align-items: center; gap: 8px;
        padding: 2px 2px 12px;
        color: var(--fg-dim); font-size: 12.5px;
    }
    .crumb__file { color: var(--fg-dim); }
    .crumb__line {
        color: var(--accent-violet);
        background: rgba(167,139,250,0.12);
        border: 1px solid var(--line-soft);
        padding: 1px 7px; border-radius: 6px;
        font-size: 11px; font-weight: 600;
    }
    .crumb__dot {
        margin-left: auto; width: 8px; height: 8px;
        background: var(--accent-cyan); border-radius: 50%;
        box-shadow: 0 0 8px rgba(103,232,249,0.55);
    }
    /* 파일명 오른쪽 캐시 비우기 버튼 — 휴지통 아이콘. .crumb__dot 의 margin-left:auto 가
       이 버튼 뒤에서 작동하므로 버튼은 파일명 바로 옆에, 점은 맨 오른쪽에 남는다. */
    .tl-clear {
        display: inline-flex; align-items: center; justify-content: center;
        width: 22px; height: 22px; padding: 0;
        background: none; border: none; border-radius: 6px;
        color: var(--fg-mute); cursor: pointer;
    }
    .tl-clear:hover { background: var(--line-soft); color: var(--fg-dim); }

    /* ── 메타 테이블 ─────────────────────────────────────────────── */
    .meta {
        display: grid; grid-template-columns: 56px 1fr; row-gap: 9px; column-gap: 12px;
        font-size: 12px;
    }
    .meta dt { color: var(--fg-mute); font-weight: 500; }
    .meta dd { margin: 0; color: var(--fg-dim); }
    .meta dd strong { color: var(--fg); font-weight: 600; }
    .meta .author { display: inline-flex; align-items: center; gap: 6px; }
    .meta a, .meta .link {
        color: var(--fg-dim); text-decoration: none; cursor: pointer;
    }
    .meta a:hover, .meta .link:hover { color: var(--fg); }
    .meta .ticket { color: var(--accent-cyan); }

    /* ── Related Changes ─────────────────────────────────────────── */
    .related__title {
        color: var(--fg-mute); font-size: 11px; font-weight: 600;
        text-transform: none; letter-spacing: 0;
        margin-bottom: 8px;
    }
    .related__list { display: flex; flex-direction: column; gap: 6px; }

    /* ── 라인 수정 이력 ──────────────────────────────────────────── */
    .history__title {
        color: var(--fg-mute); font-size: 11px; font-weight: 600;
        margin-bottom: 10px;
    }
    .history__list {
        display: flex; flex-direction: column;
        position: relative;
    }
    .hist-item {
        display: grid;
        grid-template-columns: 16px 1fr;   /* 점 + 본문. 배지는 본문 머리줄 우측으로 옮김 */
        column-gap: 8px;
        padding: 0 0 14px 0;
        cursor: default;   /* 행 본문은 클릭 동작 없음(배지만 pointer) */
        position: relative;
    }
    .hist-item:last-child { padding-bottom: 2px; }
    /* 타임라인 세로선 — 점들을 잇는다 (마지막 항목 아래로는 그리지 않음) */
    .hist-item:not(:last-child)::before {
        content: '';
        position: absolute;
        left: 4px; top: 12px; bottom: 0;
        width: 1px; background: var(--line);
    }
    .hist-item__dot {
        width: 9px; height: 9px; margin-top: 3px;
        border-radius: 50%;
        background: var(--fg-mute);
        align-self: start;
    }
    .hist-item.current .hist-item__dot {
        background: var(--accent-violet);
        box-shadow: 0 0 8px rgba(167,139,250,0.55);
    }
    .hist-item__head {
        display: flex; align-items: baseline; gap: 8px; min-width: 0;
    }
    .hist-item__hash {
        color: var(--accent-violet); font-size: 12px; font-weight: 600;
    }
    .hist-item.current .hist-item__hash { color: var(--accent-violet); }
    .hist-item__date { color: var(--fg-mute); font-size: 11px; }
    .hist-item__subject {
        color: var(--fg); font-size: 12.5px; margin-top: 3px; line-height: 1.45;
        /* 길어도 줄이지 말고 전부 보여준다. 한글은 어절(띄어쓰기) 단위로 줄바꿈(keep-all)해
           '추/가'처럼 단어 중간이 깨지지 않게 하고, 띄어쓰기 없는 긴 토큰만 강제로 줄바꿈한다. */
        white-space: normal; word-break: keep-all; overflow-wrap: break-word;
    }
    /* AI 타이틀 도착 전 — 원본 메시지를 보여주고 갈아끼우면 '글자가 바뀌어' 어색하므로,
       제목 자리에 스켈레톤만 두고 준비되면 드러낸다(로딩 후 등장). */
    .hist-item__subject.is-loading {
        color: transparent; height: 12px; max-width: 62%; margin-top: 5px;
        border-radius: 4px; background: var(--line);
        background-image: linear-gradient(90deg, transparent 0, rgba(255,255,255,0.07) 40%, transparent 80%);
        background-size: 200% 100%; background-repeat: no-repeat;
        animation: hist-skel 1.15s ease-in-out infinite;
    }
    @keyframes hist-skel { 0% { background-position: 150% 0; } 100% { background-position: -50% 0; } }
    /* 스켈레톤 → AI 타이틀로 드러날 때 살짝 페이드 인(텍스트 교체가 아니라 '등장'). */
    .hist-item__subject--ai { animation: hist-title-in .3s ease; }
    @keyframes hist-title-in { from { opacity: 0; } to { opacity: 1; } }
    .hist-item__author { color: var(--fg-mute); font-size: 11px; margin-top: 2px; }
    .hist-item__issues {
        align-self: start; margin-top: 1px;
        margin-left: auto; flex-shrink: 0;   /* 머리줄(head) 우측으로 밀착 */
        display: inline-flex; align-items: center; gap: 4px;
        padding: 2px 7px; border-radius: 6px;
        background: var(--surface); border: 1px solid var(--line);
        color: var(--fg-dim); font-size: 10.5px; white-space: nowrap;
    }
    .hist-item__issues .ico { display: inline-flex; opacity: 0.8; }
    .hist-item__issues[data-action] { cursor: pointer; }
    .hist-item__issues[data-action]:hover {
        border-color: var(--accent-violet);
        color: var(--fg);
    }
    /* 펼침 캐럿 — 클릭 시 이 커밋의 변경 사유를 지연 로드 */
    .hist-item__caret {
        margin-left: auto; cursor: pointer; color: var(--fg-mute);
        display: inline-flex; align-items: center; flex-shrink: 0;
        transition: transform .15s ease; user-select: none;
    }
    .hist-item__caret:hover { color: var(--accent-violet); }
    .hist-item.expanded .hist-item__caret { transform: rotate(90deg); }
    /* 펼친 커밋의 변경 사유 카드 */
    .hist-item__reason {
        margin-top: 7px; padding: 8px 10px;
        background: var(--callout-bg); border: 1px solid var(--line-soft);
        border-radius: 8px; color: var(--fg-dim);
        font-size: 11.5px; line-height: 1.65; cursor: default;
    }
    .hist-item__reason.loading { color: var(--fg-mute); font-style: italic; }
    .hist-item__reason code { color: var(--fg); background: var(--surface); padding: 0 4px; border-radius: 4px; }

    /* ── 작은 보조 ───────────────────────────────────────────────── */
    .hidden { display: none !important; }

    /* ── 탭 페인 ─────────────────────────────────────────────────── */
    .pane.hidden { display: none !important; }

    /* ── 타임라인 페인 ───────────────────────────────────────────── */
    /* 요약 카드·헤더는 블레임과 동일한 .callout / .crumb 스타일을 공유한다. */
    .caret {
        display: inline-block; width: 2px; height: 1em; margin-left: 2px;
        vertical-align: text-bottom; background: var(--accent-violet);
        animation: caret-blink 0.9s steps(1) infinite;
    }
    @keyframes caret-blink { 0%,49% { opacity: 1; } 50%,100% { opacity: 0; } }
    /* 마일스톤 — 범례 + 연도 그룹 + 좌측 레일(주요 변곡점 / 일반 변경) 디자인 */
    .tl-ms-head { display: flex; align-items: center; justify-content: space-between; gap: 10px; margin-bottom: 12px; }
    .tl-ms-head .related__title { margin: 0; }
    .tl-legend { display: flex; gap: 12px; font-size: 10.5px; color: var(--fg-mute); flex-shrink: 0; }
    .tl-leg { display: inline-flex; align-items: center; gap: 5px; }
    .tl-leg__dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; }
    .tl-leg__dot--major { background: #f97316; box-shadow: 0 0 0 2px rgba(249,115,22,0.22); }
    .tl-leg__dot--normal { background: var(--bg); border: 2px solid var(--fg-mute); box-sizing: border-box; }

    .tl-list { display: flex; flex-direction: column; }
    .tl-year { display: flex; align-items: baseline; justify-content: space-between;
        margin: 14px 0 8px; padding-left: 0; }
    .tl-list > .tl-year:first-child { margin-top: 0; }
    .tl-year__label { font-size: 13px; font-weight: 700; color: var(--fg); letter-spacing: 0.02em; }
    .tl-year__count { font-size: 10.5px; color: var(--fg-mute); }

    .tl-item { display: grid; grid-template-columns: 44px 16px 1fr; column-gap: 8px; align-items: stretch; }
    .tl-item__date { font-size: 10.5px; color: var(--fg-mute); text-align: right; padding-top: 1px;
        font-variant-numeric: tabular-nums; white-space: nowrap; }
    .tl-item__rail { position: relative; display: flex; justify-content: center; }
    .tl-item__rail::before { content: ''; position: absolute; top: 0; bottom: 0; left: 50%;
        transform: translateX(-50%); width: 2px; background: var(--line); }
    .tl-item:first-of-type .tl-item__rail::before { top: 5px; }
    .tl-item:last-of-type .tl-item__rail::before { bottom: auto; height: 5px; }
    .tl-item__node { position: relative; z-index: 1; margin-top: 3px; flex-shrink: 0;
        width: 7px; height: 7px; border-radius: 50%;
        background: var(--bg); border: 2px solid var(--fg-mute); box-sizing: border-box; }
    .tl-item--major .tl-item__node { width: 11px; height: 11px; margin-top: 1px; border: none; }
    .tl-item__right { min-width: 0; padding-bottom: 14px; }
    .tl-item__title { font-size: 12.5px; font-weight: 600; color: var(--fg); line-height: 1.4; }
    .tl-item--major .tl-item__title { font-weight: 700; }
    .tl-item__plus { font-weight: 800; }
    .tl-item__desc { font-size: 11.5px; color: var(--fg-dim); margin-top: 3px; line-height: 1.55; }

    /* ── 이슈 페인 (요구사항 역추적) ─────────────────────────────── */
    #is-list-view { display: flex; flex-direction: column; gap: 11px; }

    /* 목록 헤더: 파일 칩 (파일명을 제목 자리에 크게 노출) */
    .is-l-head { display: flex; flex-direction: column; gap: 8px; }
    .is-l-file {
        display: inline-flex; align-items: center; gap: 7px; align-self: flex-start;
        color: var(--fg-dim); font-size: 14px;
    }
    .is-l-file__kind {
        width: 18px; height: 18px; flex-shrink: 0;
        display: inline-flex; align-items: center; justify-content: center;
        background: #6D28D9; color: #fff; border-radius: 3px; font-size: 11px; font-weight: 700;
    }
    .is-l-file .mono { color: var(--fg); font-weight: 600; }

    /* 커밋 스코프 배너 — 파일 검색과 구분되는 보라색 콜아웃 톤. */
    .is-cbanner {
        display: flex; flex-direction: column; gap: 8px;
        padding: 11px 13px; border-radius: 11px;
        background: var(--callout-bg);
        border: 1px solid var(--line-soft);
    }
    .is-cbanner__back {
        align-self: flex-start;
        background: none; border: none; padding: 0; cursor: pointer;
        color: var(--fg-dim); font-family: inherit; font-size: 11.5px;
    }
    .is-cbanner__back:hover { color: var(--fg); }
    .is-cbanner__label {
        display: inline-flex; align-items: center; gap: 6px;
        color: var(--accent-violet); font-size: 11.5px; font-weight: 600;
    }
    .is-cbanner__label span { display: inline-flex; }
    .is-cbanner__commit {
        display: flex; align-items: baseline; gap: 8px; min-width: 0;
    }
    .is-cbanner__hash {
        flex-shrink: 0; color: var(--accent-violet); font-size: 12px; font-weight: 600;
    }
    .is-cbanner__subject {
        color: var(--fg); font-size: 12.5px; min-width: 0;
        overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
    }

    /* 검색 박스 */
    .is-search {
        display: flex; align-items: center; gap: 8px;
        padding: 7px 11px; border-radius: 9px;
        background: var(--surface); border: 1px solid var(--line);
    }
    .is-search:focus-within { border-color: var(--accent-violet); }
    .is-search__ico { display: inline-flex; color: var(--fg-mute); flex-shrink: 0; }
    .is-search input {
        flex: 1; min-width: 0; border: none; background: none; outline: none;
        color: var(--fg); font-family: inherit; font-size: 12px;
    }
    .is-search input::placeholder { color: var(--fg-mute); }

    /* 상태 필터 탭 (전체/열림/닫힘) */
    .is-filters { display: flex; gap: 4px; }
    .is-filter {
        display: inline-flex; align-items: center; gap: 5px;
        padding: 5px 9px; border-radius: 7px;
        background: transparent; border: none; cursor: pointer;
        color: var(--fg-mute); font-family: inherit; font-size: 11.5px; font-weight: 500;
        transition: background .12s, color .12s;
    }
    .is-filter:hover { background: var(--line); color: var(--fg-dim); }
    .is-filter.active { background: var(--surface); color: var(--fg); box-shadow: inset 0 0 0 1px var(--line); }
    .is-filter__n { color: var(--fg-mute); font-size: 10.5px; font-weight: 600; }
    .is-filter.active .is-filter__n { color: var(--accent-violet); }

    #is-list { display: flex; flex-direction: column; gap: 8px; }
    .is-item {
        display: flex; flex-direction: column; gap: 7px;
        padding: 12px 13px; cursor: pointer;
        background: var(--surface); border: 1px solid var(--line);
        border-radius: 10px;
        transition: border-color .12s ease, background .12s ease;
    }
    .is-item:hover { background: #1D1D21; border-color: var(--accent-violet); }
    .is-item__head { display: flex; align-items: center; gap: 7px; }
    .is-item__state {
        display: inline-flex; align-items: center; gap: 4px;
        font-size: 10.5px; font-weight: 600; color: var(--fg-mute);
    }
    .is-item__state::before { content: ''; width: 6px; height: 6px; border-radius: 50%; background: var(--fg-mute); }
    .is-item__state.open { color: #4ADE80; }
    .is-item__state.open::before { background: #4ADE80; box-shadow: 0 0 5px rgba(74,222,128,0.6); }
    .is-item__state.closed { color: #F87171; }
    .is-item__state.closed::before { background: #F87171; }
    .is-item__num { color: var(--fg-mute); font-size: 11px; font-weight: 600; }
    /* 등록일 — 머리 줄 오른쪽 끝에 옅게. */
    .is-item__date { margin-left: auto; color: var(--fg-mute); font-size: 10.5px; }
    /* 메타 칩 안의 SVG 아이콘(코멘트/첨부) 정렬 보정. */
    .is-item__metaright span svg { display: block; }
    .is-item__title {
        color: var(--fg); font-size: 13px; font-weight: 600; line-height: 1.4;
        display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;
    }
    .is-item__foot {
        display: flex; align-items: center; gap: 11px;
        color: var(--fg-mute); font-size: 10.5px;
        padding-top: 2px; border-top: 1px solid var(--line);
    }
    .is-item__foot .meta { margin-left: auto; color: var(--fg-mute); }

    /* 하단 라벨+메타 한 줄 */
    .is-item__bottom { display: flex; align-items: center; gap: 7px; }
    .is-item__labels { display: flex; flex-wrap: wrap; gap: 5px; min-width: 0; }
    .is-item__label {
        font-size: 10px; padding: 1px 7px; border-radius: 5px; white-space: nowrap;
        background: var(--surface-2); border: 1px solid var(--line); color: var(--fg-dim);
    }
    .is-item__metaright {
        margin-left: auto; flex-shrink: 0;
        display: inline-flex; align-items: center; gap: 9px;
        color: var(--fg-mute); font-size: 11.5px;
    }
    .is-item__metaright span { display: inline-flex; align-items: center; gap: 3px; }
    /* 담당자 아바타 (이름 첫 글자) */
    .is-avatar {
        width: 17px; height: 17px; border-radius: 50%; flex-shrink: 0;
        display: inline-flex; align-items: center; justify-content: center;
        color: #fff; font-size: 9px; font-weight: 700;
    }

    /* ── 이슈 상세 화면 ──────────────────────────────────────────── */
    .is-detail { display: flex; flex-direction: column; gap: 13px; }
    .is-d-nav { display: flex; align-items: center; justify-content: space-between; }
    /* 뒤로가기 링크 — 버튼 아님. 강조 색 + 굵게로 '돌아가는 링크'임을 또렷이. (이슈 상세/챗봇 공통) */
    .is-d-back, .ic-back {
        display: inline-flex; align-items: center; gap: 4px;
        background: none; border: none; padding: 2px 0; cursor: pointer;
        color: var(--accent-violet); font-size: 12.5px; font-weight: 700; font-family: inherit;
    }
    .is-d-back:hover, .ic-back:hover { color: var(--accent-cyan); text-decoration: underline; }
    .is-d-pager { display: inline-flex; align-items: center; gap: 6px; color: var(--fg-mute); font-size: 11.5px; }
    .is-d-pager button {
        background: none; border: none; cursor: pointer; color: var(--fg-mute);
        font-size: 15px; line-height: 1; padding: 0 2px; font-family: inherit;
    }
    .is-d-pager button:disabled { opacity: 0.3; cursor: default; }
    .is-d-pager button:not(:disabled):hover { color: var(--fg); }

    .is-d-head { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
    .is-d-idline { display: inline-flex; align-items: center; gap: 8px; min-width: 0; }
    .is-d-state {
        display: inline-flex; align-items: center; gap: 5px;
        padding: 2px 9px; border-radius: 999px; font-size: 11px; font-weight: 600;
        border: 1px solid var(--line); color: var(--fg-mute);
    }
    .is-d-state::before { content: ''; width: 7px; height: 7px; border-radius: 50%; background: var(--fg-mute); }
    .is-d-state.open { color: #4ADE80; border-color: rgba(74,222,128,0.4); }
    .is-d-state.open::before { background: #4ADE80; box-shadow: 0 0 6px rgba(74,222,128,0.6); }
    .is-d-state.closed { color: #F87171; border-color: rgba(248,113,113,0.4); }
    .is-d-state.closed::before { background: #F87171; }
    .is-d-num { color: var(--fg-mute); font-size: 12px; font-weight: 600; }
    .is-d-ai {
        flex-shrink: 0; display: inline-flex; align-items: center; gap: 5px;
        padding: 4px 12px; border-radius: 8px; cursor: pointer; border: none;
        color: #fff; font-size: 11.5px; font-weight: 600; font-family: inherit;
        background: var(--grad);
    }
    .is-d-ai:hover { filter: brightness(1.12); }

    /* ── 이슈 AI 챗봇 ─────────────────────────────────────────── */
    /* 채팅 화면은 뷰포트를 꽉 채우는 고정 레이아웃 — 피드만 스크롤하고 입력창은 하단 고정. */
    .is-chat {
        position: fixed; inset: 0; z-index: 30;
        display: flex; flex-direction: column;
        /* 고정 오버레이 — VSCode 사이드바 배경색으로 불투명하게(아래 내용 비침 방지). */
        background: var(--vscode-sideBar-background, #18181B); box-sizing: border-box;
        padding: 12px 14px;
    }
    .ic-head {
        flex-shrink: 0;
        display: flex; align-items: center; flex-wrap: wrap; gap: 6px 8px;
        padding-bottom: 8px; border-bottom: 1px solid var(--line);
    }
    .ic-back { flex-shrink: 0; }   /* 스타일은 위 .is-d-back, .ic-back 공통 규칙을 따른다(링크형) */
    .ic-title { display: flex; align-items: center; gap: 6px; min-width: 0; flex: 1; }
    .ic-num { color: var(--fg-mute); font-size: 12px; font-weight: 600; flex-shrink: 0; }
    .ic-name { color: var(--fg); font-size: 12px; font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .ic-learned { flex-basis: 100%; color: var(--accent-cyan); font-size: 11px; }
    .ic-feed {
        flex: 1; min-height: 0; overflow-y: auto;
        display: flex; flex-direction: column; gap: 10px; padding: 12px 2px;
    }
    .ic-msg { display: flex; }
    .ic-msg.user { justify-content: flex-end; }
    .ic-bubble {
        max-width: 88%; padding: 8px 11px; border-radius: 12px; font-size: 12.5px;
        line-height: 1.6; word-break: break-word; overflow-wrap: anywhere;
    }
    .ic-msg.user .ic-bubble { background: var(--grad); color: #fff; border-bottom-right-radius: 4px; white-space: pre-wrap; }
    .ic-msg.bot .ic-bubble { background: var(--surface); color: var(--fg); border-bottom-left-radius: 4px; }
    .ic-msg.bot .ic-bubble.error { color: #FCA5A5; }
    /* 답변(마크다운) 가독성 */
    .ic-bubble h1, .ic-bubble h2, .ic-bubble h3, .ic-bubble h4 {
        margin: 9px 0 5px; font-size: 13px; font-weight: 700; color: var(--fg); line-height: 1.4;
    }
    .ic-bubble h1 { font-size: 14px; }
    .ic-bubble p { margin: 6px 0; }
    .ic-bubble ul, .ic-bubble ol { margin: 6px 0; padding-left: 18px; }
    .ic-bubble li { margin: 3px 0; }
    .ic-bubble strong { color: var(--fg); font-weight: 700; }
    .ic-bubble em { font-style: italic; }
    .ic-bubble code {
        background: rgba(255,255,255,.09); padding: 1px 5px; border-radius: 4px;
        font-size: 11.5px; font-family: var(--vscode-editor-font-family, ui-monospace, monospace);
    }
    .ic-bubble blockquote {
        margin: 7px 0; padding: 4px 10px; border-left: 3px solid var(--accent-violet);
        color: var(--fg-dim);
    }
    .ic-bubble hr { border: none; border-top: 1px solid var(--line); margin: 9px 0; }
    .ic-bubble > :first-child { margin-top: 0; }
    .ic-bubble > :last-child { margin-bottom: 0; }
    .ic-input {
        flex-shrink: 0; display: flex; gap: 6px; align-items: flex-end;
        padding-top: 10px; border-top: 1px solid var(--line);
    }
    #ic-text {
        /* 항상 3줄 높이 고정 — 초과 시 스크롤(자동 높이 변경 없음). line-height(1.5)*3 + 패딩(16). */
        flex: 1; resize: none; box-sizing: border-box;
        height: calc(1.5em * 3 + 16px); overflow-y: auto;
        padding: 8px 10px; border-radius: 8px;
        border: 1px solid var(--line); background: var(--surface); color: var(--fg);
        font: inherit; font-size: 12.5px; line-height: 1.5;
    }
    #ic-text:focus { outline: none; border-color: var(--accent-violet); }
    .ic-send {
        flex-shrink: 0; padding: 8px 12px; border-radius: 8px; border: none; cursor: pointer;
        color: #fff; font-weight: 700; font-size: 12px; background: var(--grad);
    }
    .ic-send:hover { filter: brightness(1.12); }
    .ic-send:disabled { opacity: .5; cursor: default; filter: none; }

    .is-d-title {
        color: var(--fg); font-size: 15px; font-weight: 700; line-height: 1.4;
    }
    .is-d-title[data-url]:hover { color: var(--accent-violet); }

    .is-d-labels { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
    .is-d-label {
        font-size: 10.5px; padding: 2px 8px; border-radius: 6px;
        background: var(--surface); border: 1px solid var(--line); color: var(--fg-dim);
    }

    .is-d-meta {
        display: grid; grid-template-columns: auto 1fr; row-gap: 8px; column-gap: 14px;
        font-size: 12px; margin: 0;
        padding: 11px 13px; background: var(--surface); border: 1px solid var(--line); border-radius: 9px;
    }
    .is-d-meta dt { color: var(--fg-mute); }
    .is-d-meta dd { margin: 0; color: var(--fg-dim); }
    .is-d-meta dd strong { color: var(--fg); font-weight: 600; }
    .is-d-meta dd .is-avatar { margin-right: 5px; vertical-align: -4px; }

    .is-d-body { color: var(--fg-dim); font-size: 12.5px; line-height: 1.7; word-break: break-word; }
    .is-d-body code { background: var(--code-bg); color: var(--code-fg); padding: 1px 5px; border-radius: 4px; font-size: 11.5px; }
    .is-d-quote {
        margin: 9px 0; padding: 9px 13px; border-left: 2px solid var(--accent-violet);
        background: var(--surface); border-radius: 0 8px 8px 0; color: var(--fg-dim); font-size: 12px; line-height: 1.65;
    }

    /* 섹션 헤더(첨부/활동) — 위에 구분선을 둬 본문과 또렷이 가른다. */
    .is-d-sec-title {
        margin-top: 5px; padding-top: 15px; border-top: 1px solid var(--line);
        color: var(--fg); font-size: 12.5px; font-weight: 700; letter-spacing: -0.01em;
        display: flex; align-items: center; gap: 6px;
    }
    .is-d-atts { display: flex; flex-direction: column; gap: 6px; margin-top: 2px; }
    .is-d-att {
        display: flex; align-items: center; gap: 10px;
        padding: 9px 12px; background: var(--surface); border: 1px solid var(--line);
        border-radius: 9px; cursor: pointer;
    }
    .is-d-att:hover { border-color: var(--accent-violet); }
    .is-d-att__ico {
        flex-shrink: 0; width: 26px; height: 30px; border-radius: 4px;
        background: #DC2626; color: #fff; font-size: 8px; font-weight: 700;
        display: flex; align-items: center; justify-content: center;
    }
    .is-d-att__name { color: var(--fg); font-size: 12px; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .is-d-att__meta { color: var(--fg-mute); font-size: 10.5px; margin-top: 1px; }
    /* 이미지 첨부 — 칩 대신 인라인 미리보기(클릭 시 원본 열기). */
    .is-d-img {
        margin: 0; border: 1px solid var(--line); border-radius: 9px;
        overflow: hidden; cursor: pointer; background: var(--surface-2);
    }
    .is-d-img:hover { border-color: var(--accent-violet); }
    .is-d-img img { display: block; width: 100%; max-height: 320px; object-fit: contain; }
    .is-d-img figcaption {
        padding: 6px 10px; color: var(--fg-mute); font-size: 10.5px;
        border-top: 1px solid var(--line); overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
    }
    .is-d-img img { cursor: zoom-in; }
    /* 본문 안 인라인 이미지(HTML <img> / 마크다운 ![](url)). 클릭 시 확대 팝업. */
    .is-d-bimg {
        display: block; max-width: 100%; max-height: 280px; object-fit: contain;
        border: 1px solid var(--line); border-radius: 9px; margin: 8px 0;
        background: var(--surface-2); cursor: zoom-in;
    }
    .is-d-bimg-fail {
        display: inline-block; margin: 6px 0; color: var(--accent-violet);
        font-size: 12px; cursor: pointer; text-decoration: underline;
    }
    /* 확대 팝업(라이트박스) — 화면 전체를 덮고, 아무 데나 누르면 닫힌다. */
    .is-lightbox {
        position: fixed; inset: 0; z-index: 50; padding: 24px;
        background: rgba(0,0,0,0.82); cursor: zoom-out;
        display: flex; align-items: center; justify-content: center;
    }
    .is-lightbox.hidden { display: none; }
    .is-lightbox img {
        max-width: 100%; max-height: 100%; border-radius: 6px;
        box-shadow: 0 8px 40px rgba(0,0,0,0.55);
    }
    .is-lightbox__hint {
        position: fixed; bottom: 16px; left: 0; right: 0; text-align: center;
        color: rgba(255,255,255,0.6); font-size: 11px; pointer-events: none;
    }

    /* ── 활동 타임라인 (코멘트 + 시스템 이벤트) ─────────────────── */
    .is-d-feed { display: flex; flex-direction: column; gap: 12px; }
    /* 시스템 이벤트 줄 — 작은 점 + 흐린 한 줄. */
    .is-d-ev { display: flex; align-items: baseline; gap: 8px; padding-left: 2px; }
    .is-d-ev__dot {
        flex-shrink: 0; width: 5px; height: 5px; border-radius: 50%;
        background: var(--line); transform: translateY(-2px);
    }
    .is-d-ev__txt { color: var(--fg-mute); font-size: 11.5px; line-height: 1.6; word-break: break-word; }
    .is-d-ev__txt b { color: var(--fg-dim); font-weight: 600; }
    .is-d-ev__txt code { background: var(--code-bg); color: var(--code-fg); padding: 0 4px; border-radius: 4px; font-size: 10.5px; }
    .is-d-ev__date { color: var(--fg-mute); }
    /* 사람 코멘트 카드 */
    .is-cmt {
        display: flex; flex-direction: column; gap: 7px;
        padding: 11px 13px; background: var(--surface); border: 1px solid var(--line); border-radius: 9px;
    }
    .is-cmt__head { display: flex; align-items: center; gap: 7px; }
    .is-cmt__name { color: var(--fg); font-size: 12px; font-weight: 600; }
    .is-cmt__date { margin-left: auto; color: var(--fg-mute); font-size: 10.5px; }
    .is-cmt__body { color: var(--fg-dim); font-size: 12.5px; line-height: 1.65; word-break: break-word; }
    .is-cmt__body code { background: var(--code-bg); color: var(--code-fg); padding: 1px 5px; border-radius: 4px; font-size: 11.5px; }

    /* ── 로딩 스피너 ─────────────────────────────────────────────── */
    .spinner {
        display: inline-block; width: 13px; height: 13px; vertical-align: -2px;
        border: 2px solid var(--line); border-top-color: var(--accent-violet);
        border-radius: 50%; animation: spin .75s linear infinite; margin-right: 6px;
    }
    @keyframes spin { to { transform: rotate(360deg); } }

    /* ── 마일스톤 로딩 바 (요약이 타이핑되는 동안 하단에 표시) ─────── */
    .tl-ms-loading { margin-top: 10px; }
    .tl-loadbar {
        position: relative; height: 3px; border-radius: 3px;
        background: var(--line); overflow: hidden;
    }
    .tl-loadbar::before {
        content: ''; position: absolute; top: 0; bottom: 0; left: -40%;
        width: 40%; border-radius: 3px; background: var(--accent-violet);
        animation: tl-loadbar 1.05s ease-in-out infinite;
    }
    @keyframes tl-loadbar {
        0%   { left: -40%; }
        100% { left: 100%; }
    }
    .tl-ms-loading__txt {
        margin-top: 7px; font-size: 11px; color: var(--fg-mute);
    }
</style>
</head>
<body>
    <nav class="tabs hidden">
        <button class="tab active" data-tab="blame"><span class="tab__ico" id="ico-tab-blame"></span>돋보기</button>
        <button class="tab" data-tab="timeline"><span class="tab__ico" id="ico-tab-timeline"></span>타임라인</button>
        <button class="tab" data-tab="issue"><span class="tab__ico" id="ico-tab-issue"></span>이슈</button>
    </nav>

    <!-- ─────────────── 블레임 탭 ─────────────── -->
    <div id="pane-blame" class="pane">
    <!-- 설치 직후 첫 화면(환영/온보딩). 탭 바는 이 상태에서 숨기고, 분석 시작·칩 클릭 시 노출한다. -->
    <div id="empty" class="hero">
        <div class="hero__badge"><span id="ico-hero-shield"></span></div>
        <h1 class="hero__title">이 파일, 왜 이렇게 짰을까?</h1>
        <p class="hero__desc">CodeWhy가 커밋 히스토리와 기획서를 읽어<br/><strong>모든 결정의 이유</strong>를 이 자리에 정리해 드립니다.</p>
        <button class="hero__cta" data-action="analyzeFile"><span id="ico-hero-spark"></span> 이 파일 분석하기</button>
        <div class="hero__status"><span id="ico-hero-check"></span> 저장소 연결됨</div>
    </div>

    <div id="info" class="info hidden">
        <span class="info__icon" id="ico-info"></span>
        <div class="info__text" id="info-text"></div>
    </div>

    <div id="content" class="body hidden">
        <div class="crumb">
            <span class="mono crumb__file" id="file-name"></span>
            <span class="crumb__line mono" id="file-line"></span>
            <button class="tl-clear" id="bl-clear" data-action="clearBlameCache" title="이 파일의 돋보기 캐시 비우기"><span id="ico-bl-clear"></span></button>
            <span class="crumb__dot"></span>
        </div>

        <section class="callout">
            <div class="callout__author">
                <span class="callout__avatar" id="ca-avatar"></span>
                <span class="callout__who">
                    <strong class="callout__name" id="ca-name"></strong>
                    <span class="callout__when" id="ca-when"></span>
                </span>
            </div>
            <div class="callout__lead" id="narrative"></div>
            <section class="callout__detail hidden" id="ca-detail-sec">
                <div class="callout__detail-label">자세한 설명</div>
                <div class="callout__detail-body" id="ca-detail"></div>
            </section>
            <button class="callout__more expanded hidden" id="callout-more" data-action="toggleCallout">접기</button>
            <div class="callout__chips hidden" id="callout-chips"></div>
        </section>

        <section id="history-wrap" class="hidden">
            <div class="history__title">라인 수정 이력</div>
            <div class="history__list" id="history-list"></div>
        </section>
    </div>
    </div><!-- /#pane-blame -->

    <!-- ─────────────── 타임라인 탭 ─────────────── -->
    <div id="pane-timeline" class="pane hidden">
        <div id="tl-empty" class="empty">파일을 연 뒤 <strong>타임라인</strong> 탭을 누르면<br/>이 파일의 변경 역사를 요약해 드립니다.</div>
        <div id="tl-body" class="body hidden">
            <div class="crumb">
                <span class="mono crumb__file" id="tl-file"></span>
                <button class="tl-clear" id="tl-clear" data-action="clearTimelineCache" title="이 파일의 타임라인 캐시 비우기"><span id="ico-tl-clear"></span></button>
                <span class="crumb__dot"></span>
            </div>
            <section class="callout">
                <div class="callout__lead" id="tl-summary"></div>
                <section class="callout__detail hidden" id="tl-detail-sec">
                    <div class="callout__detail-label">자세한 설명</div>
                    <div class="callout__detail-body" id="tl-detail"></div>
                </section>
                <button class="callout__more expanded hidden" id="tl-more" data-action="toggleCallout">접기</button>
            </section>
            <section id="tl-ms-wrap" class="hidden">
                <div class="tl-ms-head">
                    <div class="related__title">주요 마일스톤</div>
                    <div class="tl-legend">
                        <span class="tl-leg"><span class="tl-leg__dot tl-leg__dot--major"></span>주요 변곡점</span>
                        <span class="tl-leg"><span class="tl-leg__dot tl-leg__dot--normal"></span>일반 변경</span>
                    </div>
                </div>
                <div id="tl-ms-loading" class="tl-ms-loading hidden">
                    <div class="tl-loadbar"></div>
                    <div class="tl-ms-loading__txt">주요 마일스톤을 정리하고 있습니다…</div>
                </div>
                <div class="tl-list" id="tl-list"></div>
            </section>
        </div>
    </div><!-- /#pane-timeline -->

    <!-- ─────────────── 이슈 탭 ─────────────── -->
    <div id="pane-issue" class="pane hidden">
        <div id="is-empty" class="empty"><strong>이슈</strong> 탭을 누르면 이 파일과<br/>연관된 GitHub Issue·기획서를 찾아 드립니다.</div>
        <div id="is-loading" class="empty hidden"><span class="spinner"></span> 연관 이슈 찾는 중…</div>
        <div id="is-body" class="body hidden">
            <div id="is-list-view">
                <!-- 커밋 스코프 배너 — '라인 수정 이력'의 '이슈 N' 배지로 들어왔을 때만 노출.
                     파일 검색(아래 파일 칩/검색/필터)과 시각적으로 또렷이 구분한다. -->
                <div id="is-commit-banner" class="is-cbanner hidden">
                    <button class="is-cbanner__back" data-action="issueBackToFile">‹ 파일 전체 이슈</button>
                    <div class="is-cbanner__label"><span id="ico-is-cbanner"></span> 이 커밋이 참조한 이슈</div>
                    <div class="is-cbanner__commit">
                        <span class="is-cbanner__hash mono" id="is-cb-hash"></span>
                        <span class="is-cbanner__subject" id="is-cb-subject"></span>
                    </div>
                </div>
                <div class="is-l-head">
                    <span class="is-l-file"><span class="is-l-file__kind" id="is-l-kind">K</span><span class="mono" id="is-l-fname"></span></span>
                </div>
                <div class="is-search">
                    <span class="is-search__ico" id="ico-is-search"></span>
                    <input id="is-search-input" type="text" placeholder="요구사항 검색…" autocomplete="off" spellcheck="false" />
                </div>
                <div class="is-filters" id="is-filters">
                    <button class="is-filter active" data-filter="all">전체 <span class="is-filter__n" data-count="all">0</span></button>
                    <button class="is-filter" data-filter="open">열림 <span class="is-filter__n" data-count="open">0</span></button>
                    <button class="is-filter" data-filter="closed">닫힘 <span class="is-filter__n" data-count="closed">0</span></button>
                </div>
                <div class="related__list" id="is-list"></div>
                <div id="is-list-empty" class="empty hidden">검색 결과가 없습니다.</div>
            </div>
            <div id="is-detail-view" class="is-detail hidden"></div>
            <div id="is-chat-view" class="is-chat hidden">
                <div class="ic-head">
                    <button class="ic-back" data-action="issueChatBack">‹ 이슈로</button>
                    <div class="ic-title"><span id="ic-num" class="ic-num"></span><span id="ic-name" class="ic-name"></span></div>
                    <div id="ic-learned" class="ic-learned"></div>
                </div>
                <div id="ic-feed" class="ic-feed"></div>
                <div class="ic-input">
                    <textarea id="ic-text" rows="3" placeholder="이 이슈의 맥락에서 계속 질문하세요…" autocomplete="off" spellcheck="false"></textarea>
                    <button id="ic-send" class="ic-send" data-action="issueChatSend">↑ 전송</button>
                </div>
            </div>
        </div>
    </div><!-- /#pane-issue -->

<script nonce="${nonce}">
    const vscode = acquireVsCodeApi();

    // 웹뷰 스크립트에서 던져진 에러를 확장 Output 채널로 되쏜다(웹뷰 DevTools 없이 진단).
    window.addEventListener('error', function (e) {
        try {
            vscode.postMessage({
                type: 'webview-error',
                payload: (e && e.message) + ' @ ' + (e && e.filename) + ':' + (e && e.lineno),
            });
        } catch (_) { /* noop */ }
    });

    // ── 아이콘들 (인라인 SVG) ───────────────────────────────────────
    const ICON = {
        spark:  '<svg width="13" height="13" viewBox="0 0 16 16" fill="currentColor"><path d="M8 1l1.5 4.5L14 7l-4.5 1.5L8 13l-1.5-4.5L2 7l4.5-1.5L8 1z"/></svg>',
        copy:   '<svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.3"><rect x="5" y="5" width="9" height="9" rx="1.5"/><path d="M2 11V3.5A1.5 1.5 0 0 1 3.5 2H11"/></svg>',
        doc:    '<svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.2"><path d="M3 1h7l3 3v11H3V1z"/><path d="M10 1v4h3M5 8h6M5 10h6M5 12h4"/></svg>',
        branch: '<svg width="13" height="13" viewBox="0 0 16 16" fill="currentColor"><path d="M5 3a1.5 1.5 0 1 0-2 0v8a1.5 1.5 0 1 0 1 0V8h3a3 3 0 0 0 3-3V4.92a1.5 1.5 0 1 0-1 0V5a2 2 0 0 1-2 2H4V3z"/></svg>',
        shield: '<svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.2"><path d="M8 1l6 2v5c0 4-2.8 6.6-6 7-3.2-.4-6-3-6-7V3l6-2z"/></svg>',
        commit: '<svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.3"><circle cx="8" cy="8" r="2.5"/><path d="M0 8h5M11 8h5"/></svg>',
        clock:  '<svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.2"><circle cx="8" cy="8" r="6.5"/><path d="M8 4.5V8l2.5 1.5"/></svg>',
        issue:  '<svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.3"><circle cx="8" cy="8" r="6.5"/><circle cx="8" cy="8" r="1.6" fill="currentColor" stroke="none"/></svg>',
        caret:  '<svg width="9" height="9" viewBox="0 0 16 16" fill="currentColor"><path d="M6 3.5l5.5 4.5L6 12.5z"/></svg>',
        search: '<svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.3"><circle cx="7" cy="7" r="4.5"/><path d="M10.5 10.5L14 14"/></svg>',
        check:'<svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M3 8.5l3.2 3.2L13 4.5"/></svg>',
        shieldBig: '<svg width="30" height="30" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.1"><path d="M8 1l6 2v5c0 4-2.8 6.6-6 7-3.2-.4-6-3-6-7V3l6-2z"/><path d="M5.5 8l1.8 1.8L11 6" stroke-width="1.3"/></svg>',
        sparkBig: '<svg width="15" height="15" viewBox="0 0 16 16" fill="currentColor"><path d="M8 1l1.5 4.5L14 7l-4.5 1.5L8 13l-1.5-4.5L2 7l4.5-1.5L8 1z"/></svg>',
        // 코멘트 수 칩 — 이모지(💬) 대신 SVG 말풍선(웹뷰 폰트에서 깨지지 않음).
        comment: '<svg width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.2"><path d="M2 4a1 1 0 0 1 1-1h10a1 1 0 0 1 1 1v6a1 1 0 0 1-1 1H6.5L4 13.5V11H3a1 1 0 0 1-1-1V4z"/></svg>',
        // 첨부 수 칩 — 이모지(📎) 대신 SVG 클립.
        clip: '<svg width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round"><path d="M12.5 7.2l-5 5a2.8 2.8 0 0 1-4-4l5.2-5.2a1.8 1.8 0 0 1 2.6 2.6l-5.2 5.2a.8.8 0 0 1-1.2-1.2l4.6-4.6"/></svg>',
        // 팀 칩용 — 사람 둘.
        users: '<svg width="11" height="11" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.2"><circle cx="6" cy="5" r="2.2"/><path d="M2 13c0-2.2 1.8-3.6 4-3.6s4 1.4 4 3.6"/><path d="M10.6 3.2a2.2 2.2 0 0 1 0 4.1M11 9.6c1.8.2 3 1.5 3 3.4"/></svg>',
        // 타임라인 캐시 비우기 버튼용 — 휴지통(삭제).
        trash: '<svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round"><path d="M2.5 4h11M6 4V2.5h4V4M4 4l.6 9a1 1 0 0 0 1 1h4.8a1 1 0 0 0 1-1L12 4M6.5 7v4M9.5 7v4"/></svg>',
    };
    // 아이콘 주입은 보조 장식이므로, 한 요소가 없더라도 핸드셰이크(ready)까지 죽지 않게 격리한다.
    try {
        const setIcon = (id, svg) => { const el = document.getElementById(id); if (el) { el.innerHTML = svg; } };
        setIcon('ico-info', ICON.spark);
        setIcon('ico-tab-blame', ICON.search);   // '돋보기' 탭 — 용어에 맞춰 돋보기 아이콘
        setIcon('ico-tab-timeline', ICON.clock);
        setIcon('ico-tab-issue', ICON.branch);
        setIcon('ico-hero-shield', ICON.shieldBig);
        setIcon('ico-hero-spark', ICON.sparkBig);
        setIcon('ico-hero-check', ICON.check);
        setIcon('ico-is-search', ICON.search);
        setIcon('ico-is-cbanner', ICON.issue);
        setIcon('ico-tl-clear', ICON.trash);
        setIcon('ico-bl-clear', ICON.trash);   // 돋보기 파일명 옆 캐시 비우기 — 타임라인과 동일 휴지통
    } catch (err) {
        vscode.postMessage({ type: 'webview-error', payload: '아이콘 초기화 실패: ' + String(err) });
    }

    // ── 메시지 수신 ────────────────────────────────────────────────
    window.addEventListener('message', (e) => {
        const msg = e.data;
        switch (msg.type) {
            // ── 블레임 ──
            case 'render': render(msg.payload); break;
            case 'blStreaming': blStreaming(msg.payload); break;
            case 'blDelta': blDelta(msg.payload.delta); break;
            case 'blResult': blResult(msg.payload); break;
            case 'historyReason': fillHistoryReason(msg.payload.hash, msg.payload.reason); break;
            case 'historyTitles': applyHistoryTitles(msg.payload.titles); break;
            case 'info': showInfo(msg.payload.message); break;
            case 'empty':
                document.getElementById('info').classList.add('hidden');
                document.getElementById('empty').classList.remove('hidden');
                document.getElementById('content').classList.add('hidden');
                revealTabs(false);   // 환영 화면으로 복귀 — 탭 바 숨김
                break;
            // ── 탭 전환(확장 → 웹뷰) ──
            case 'activateTab': showTab(msg.payload.tab); break;
            // ── 타임라인 ──
            case 'tlStreaming': tlStreaming(msg.payload); break;
            case 'tlDelta': tlDelta(msg.payload.delta); break;
            case 'tlSummaryDone': tlSummaryDone(msg.payload); break;
            case 'tlResult': tlResult(msg.payload); break;
            case 'tlEmpty': tlEmpty(msg.payload && msg.payload.message); break;
            // ── 이슈 ──
            case 'isLoading': isLoading(); break;
            case 'isResult': isResult(msg.payload); break;
            case 'isEmpty': isEmpty(msg.payload && msg.payload.message); break;
            case 'isCommitLoading': isCommitLoading(msg.payload); break;
            case 'isCommitResult': isCommitResult(msg.payload); break;
            // ── 이슈 챗봇 스트림 ──
            case 'issueChat': onChatFrame(msg.payload); break;
        }
    });

    // ─────────────────── 타임라인 렌더 ───────────────────
    let tlText = '';
    // 상단 요약이 summaryDone 으로 '확정'되면 잠근다 — 이후 마일스톤 토큰(tlDelta)은 상단을 건드리지 않는다.
    let tlSummaryLocked = false;
    function tlShow(which) {
        document.getElementById('tl-empty').classList.toggle('hidden', which !== 'empty');
        document.getElementById('tl-body').classList.toggle('hidden', which === 'empty');
    }
    // 스트리밍 중엔 raw JSON 전체가 아니라 summary 값만 뽑아 위에 타이핑하고,
    // 마일스톤 자리에는 로딩 바를 돌린다(카드는 done(tlResult)에서 확정).
    function tlPaintStreamingSummary() {
        const sum = tlExtractStreamingSummary(tlText);
        document.getElementById('tl-summary').innerHTML = sum
            ? renderBold(sum) + '<span class="caret"></span>'
            : '<span class="spinner"></span>AI가 소스 코드를 분석 중입니다…';
    }
    function tlStreaming(p) {
        tlText = p.text || '';
        tlSummaryLocked = false;
        document.getElementById('tl-file').textContent = p.fileName || '';
        // 핵심/배경 분리·접기는 summaryDone/done 에서 확정하고, 스트리밍 중엔 캐럿만 띄운다.
        tlPaintStreamingSummary();
        document.getElementById('tl-detail-sec').classList.add('hidden');
        document.getElementById('tl-more').classList.add('hidden');
        // 마일스톤 영역: 헤더+로딩 바만 먼저 노출, 카드 목록은 비워 둔다.
        document.getElementById('tl-list').innerHTML = '';
        document.getElementById('tl-ms-loading').classList.remove('hidden');
        document.getElementById('tl-ms-wrap').classList.remove('hidden');
        tlShow('body');
    }
    function tlDelta(delta) {
        tlText += delta;
        // 상단이 확정된 뒤(마일스톤 토큰 구간)에는 상단을 다시 그리지 않는다.
        if (tlSummaryLocked) { return; }
        tlPaintStreamingSummary();
    }
    // 상단 요약 조기 확정 — 캐럿을 거두고 핵심/배경으로 가른다. 마일스톤은 로딩 바 유지.
    function tlSummaryDone(p) {
        if (p && p.fileName) { document.getElementById('tl-file').textContent = p.fileName; }
        tlFillSummary((p && p.summary) || tlExtractStreamingSummary(tlText));
        tlSummaryLocked = true;
        document.getElementById('tl-ms-loading').classList.remove('hidden');
        document.getElementById('tl-ms-wrap').classList.remove('hidden');
        tlShow('body');
    }
    function tlResult(p) {
        document.getElementById('tl-file').textContent = p.fileName || '';
        // 요약을 핵심 한 줄 / 자세한 배경으로 가르고 캐럿을 거둔다(블레임 콜아웃과 동일).
        // 방어: 백엔드가 (드물게) 파싱 못 한 raw JSON 을 summary 로 보내도 골격이 안 새게 한 번 더 건진다.
        let summary = p.summary || '';
        if (/^\s*\{[\s\S]*"summary"\s*:/.test(summary)) { summary = tlExtractStreamingSummary(summary) || summary; }
        tlFillSummary(summary);
        const wrap = document.getElementById('tl-ms-wrap');
        const list = document.getElementById('tl-list');
        list.innerHTML = '';
        document.getElementById('tl-ms-loading').classList.add('hidden');  // 로딩 바 종료
        // 최신→오래된 내림차순(위가 최근, 아래가 과거).
        const ms = (p.milestones || []).slice().sort((a, b) => String(b.date).localeCompare(String(a.date)));
        if (ms.length) {
            wrap.classList.remove('hidden');
            renderMilestones(list, ms);
        } else {
            wrap.classList.add('hidden');
        }
        tlShow('body');
    }
    function tlEmpty(message) {
        if (message) { document.getElementById('tl-empty').innerHTML = decorate(message); }
        tlShow('empty');
    }
    // 주요 변곡점(major) 노드/+아이콘 색상 — 변곡점이 나올 때마다 순환해 서로 구분된다.
    const TL_MAJOR_COLORS = ['#f97316','#2cb8b8','#8b5cf6','#e05454','#16a34a','#3b82f6'];
    // 연도 그룹 헤더 + 마일스톤 항목을 순서대로 list 에 그린다.
    function renderMilestones(list, ms) {
        // 폴백 — 백엔드 major 가 하나도 없으면(옛 캐시 등) 가장 오래된 항목을 변곡점으로 둔다.
        const hasMajor = ms.some(m => !!m.major);
        // 정렬 방향과 무관하게 '가장 오래된 날짜'를 폴백 변곡점으로 잡는다.
        const oldestDate = ms.reduce((min, m) => String(m.date) < min ? String(m.date) : min, String(ms[0].date));
        // 연도별 변경 개수 미리 집계('N개 변경' 배지용).
        const yearCount = {};
        ms.forEach(m => { const y = String(m.date).slice(0, 4); yearCount[y] = (yearCount[y] || 0) + 1; });
        let curYear = '';
        let majorIdx = 0;
        ms.forEach((m, i) => {
            const y = String(m.date).slice(0, 4) || '----';
            if (y !== curYear) {
                curYear = y;
                list.appendChild(renderYearHead(y, yearCount[y]));
            }
            const isMajor = hasMajor ? !!m.major : (String(m.date) === oldestDate);
            list.appendChild(renderMilestone(m, isMajor, isMajor ? majorIdx++ : -1));
        });
    }
    function renderYearHead(year, count) {
        const el = document.createElement('div');
        el.className = 'tl-year';
        el.innerHTML = '<span class="tl-year__label"></span><span class="tl-year__count"></span>';
        el.querySelector('.tl-year__label').textContent = year;
        el.querySelector('.tl-year__count').textContent = (count || 0) + '개 변경';
        return el;
    }
    function renderMilestone(m, isMajor, majorIdx) {
        const el = document.createElement('div');
        el.className = 'tl-item' + (isMajor ? ' tl-item--major' : '');
        const color = isMajor ? TL_MAJOR_COLORS[(majorIdx >= 0 ? majorIdx : 0) % TL_MAJOR_COLORS.length] : '';
        const md = String(m.date || '').split('-');
        const mmdd = (md[1] || '') + (md[2] ? '-' + md[2] : '');   // MM-DD
        // 타이틀/내용 분리 — 줄바꿈 우선, 없으면 한 문장을 타이틀/내용으로 가른다(폴백).
        const split = tlMilestoneSplit(m.description || '');
        const title = split.lead;
        const body = split.rest;
        const nodeStyle = isMajor ? ' style="background:' + color + ';box-shadow:0 0 0 3px ' + color + '33"' : '';
        const plus = isMajor ? '<span class="tl-item__plus" style="color:' + color + '">+</span> ' : '';
        el.innerHTML =
            '<span class="tl-item__date"></span>' +
            '<div class="tl-item__rail"><span class="tl-item__node"' + nodeStyle + '></span></div>' +
            '<div class="tl-item__right">' +
                '<div class="tl-item__title">' + plus + '<span class="tl-item__ttext"></span></div>' +
                (body ? '<div class="tl-item__desc"></div>' : '') +
            '</div>';
        el.querySelector('.tl-item__date').textContent = mmdd;
        el.querySelector('.tl-item__ttext').textContent = title;
        if (body) { el.querySelector('.tl-item__desc').textContent = body; }
        return el;
    }
    function renderBold(t) {
        return decorate(t).replace(/\\*\\*(.+?)\\*\\*/g, '<strong>$1</strong>');
    }
    // 이슈 본문 → HTML. 연속된 "> …" 줄은 인용 박스(.is-d-quote)로 묶고,
    // 나머지 줄은 renderBold(=decorate+굵게)로 그린다. (마크다운 최소 지원)
    // URL 끝에 붙은 따옴표/꺾쇠/공백을 떨군다 — HTML <img src="..."> 추출 시 닫는 따옴표가
    // 섞여 들어오는 케이스(백엔드 정규식 보정 전 데이터 포함)를 프런트에서도 방어한다.
    function cleanUrl(u) { return String(u || '').replace(/[\\s"'<>]+$/g, ''); }
    // 속성값 escape — innerHTML 문자열에 URL 을 안전히 끼우기 위함.
    function attrEsc(s) {
        return String(s).replace(/&/g, '&amp;').replace(/"/g, '&quot;')
            .replace(/</g, '&lt;').replace(/>/g, '&gt;');
    }
    // 본문 인라인 이미지 1장 — 클릭하면 라이트박스로 확대(zoomImage).
    function bodyImgHTML(url) {
        const safe = attrEsc(cleanUrl(url));
        return '<img class="is-d-bimg" data-action="zoomImage" data-url="' + safe + '" src="' + safe + '" alt="첨부 이미지" loading="lazy"/>';
    }
    // 인용/개행만 처리하는 본문 텍스트 렌더(이미지 토큰을 걷어낸 조각에 적용).
    function renderBodyText(text) {
        const lines = String(text).split('\\n');   // 실제 개행으로 분리
        let html = '';
        let quote = [];
        const flush = () => {
            if (quote.length) {
                html += '<blockquote class="is-d-quote">' + quote.map(renderBold).join('<br/>') + '</blockquote>';
                quote = [];
            }
        };
        lines.forEach(ln => {
            const m = /^\\s*>\\s?(.*)$/.exec(ln);
            if (m) { quote.push(m[1]); }
            else { flush(); html += ln.trim() ? (renderBold(ln) + '<br/>') : '<br/>'; }
        });
        flush();
        return html;
    }
    // 본문을 이미지 토큰(HTML <img src> / 마크다운 ![alt](url)) 기준으로 쪼개,
    // 이미지는 인라인 미리보기로, 나머지는 텍스트로 렌더한다.
    // 렌더한 이미지 URL 은 imgUrlsOut(있으면)에 모아 첨부 중복 제거에 쓴다.
    function renderIssueBodyHTML(text, imgUrlsOut) {
        const src = String(text);
        const IMG_TOKEN = /<img\\b[^>]*?\\bsrc\\s*=\\s*["']([^"'\\s>]+)["']?[^>]*>|!\\[[^\\]]*\\]\\((https?:\\/\\/[^)\\s]+)\\)/gi;
        let html = '';
        let last = 0;
        let m;
        while ((m = IMG_TOKEN.exec(src))) {
            const seg = src.slice(last, m.index);
            if (seg.trim()) { html += renderBodyText(seg); }
            const url = cleanUrl(m[1] || m[2]);
            if (url) { html += bodyImgHTML(url); if (imgUrlsOut) { imgUrlsOut.push(url); } }
            last = m.index + m[0].length;
        }
        const tail = src.slice(last);
        if (tail.trim() || !html) { html += renderBodyText(tail); }
        return html;
    }

    // ─────────────────── 이슈 렌더 ───────────────────
    function isShow(which) {
        document.getElementById('is-empty').classList.toggle('hidden', which !== 'empty');
        document.getElementById('is-loading').classList.toggle('hidden', which !== 'loading');
        document.getElementById('is-body').classList.toggle('hidden', which !== 'body');
    }
    function isLoading() { isShow('loading'); }
    function isEmpty(message) {
        if (message) { document.getElementById('is-empty').innerHTML = decorate(message); }
        isShow('empty');
    }
    // 이슈 탭 상태 — 목록과 상세를 한 데이터(isDocs)로 공유하고 isIndex 로 상세 대상을 가린다.
    // isQuery(검색어)·isFilter(상태 탭)는 목록 뷰 상태로, 데이터 재요청 없이 클라이언트에서만 거른다.
    let isDocs = [];
    let isLine = 0;
    let isFileName = '';
    let isIndex = 0;
    let isQuery = '';
    let isFilter = 'all';   // all | open | closed
    let isScope = 'file';   // 'file'(파일 검색) | 'commit'(라인 이력의 '이슈 N' 배지)
    // 현재 목록에 '보이는' 항목들의 원본 isDocs 인덱스(필터+검색 적용 순서).
    // 상세 이전/다음은 isDocs 전체가 아니라 이 부분집합 안에서만 이동해야 한다(버그 #4).
    let isVisible = [];
    // 비었으면(예: 커밋 스코프) 전체를 보이는 것으로 간주한다.
    function visibleList() { return isVisible.length ? isVisible : isDocs.map((_, i) => i); }

    // 이슈 상태를 필터 버킷으로 분류한다. 백엔드 state(open/closed) 기준.
    function issueBucket(d) {
        const s = String((d && d.state) || '').toLowerCase();
        if (s === 'closed') { return 'closed'; }
        return 'open';
    }

    function isResult(p) {
        isScope = 'file';
        setIssueScope('file');
        isDocs = p.documents || [];
        isLine = p.line || 0;
        isFileName = p.fileName || '';
        isIndex = 0;
        isQuery = '';
        isFilter = 'all';
        const input = document.getElementById('is-search-input');
        if (input) { input.value = ''; }
        document.getElementById('is-l-fname').textContent = isFileName;
        document.getElementById('is-l-kind').textContent = fileKind(isFileName);
        renderIssueList();
        showIssueList();
        isShow('body');
    }

    // 이슈 페인 스코프 토글 — 커밋 스코프에선 파일 칩/검색/필터를 숨기고 커밋 배너만 노출한다.
    function setIssueScope(scope) {
        const commit = scope === 'commit';
        document.getElementById('is-commit-banner').classList.toggle('hidden', !commit);
        document.querySelector('.is-l-head').classList.toggle('hidden', commit);
        document.querySelector('.is-search').classList.toggle('hidden', commit);
        document.getElementById('is-filters').classList.toggle('hidden', commit);
    }
    function fillCommitBanner(hash, subject) {
        document.getElementById('is-cb-hash').textContent = (hash || '').slice(0, 7);
        document.getElementById('is-cb-subject').textContent = subject || '';
    }
    // 커밋 스코프 로딩 — 배너를 먼저 띄우고 목록 자리에 스피너만.
    function isCommitLoading(p) {
        isScope = 'commit';
        fillCommitBanner(p.hash, p.subject);
        setIssueScope('commit');
        document.getElementById('is-list').innerHTML =
            '<div class="empty"><span class="spinner"></span> 이 커밋의 연관 이슈 찾는 중…</div>';
        document.getElementById('is-list-empty').classList.add('hidden');
        showIssueList();
        isShow('body');
    }
    // 커밋 스코프 결과 — 파일 검색과 같은 카드 렌더를 재사용하되, 헤더만 커밋 배너로 바꾼다.
    function isCommitResult(p) {
        isScope = 'commit';
        isDocs = p.documents || [];
        isIndex = 0;
        isVisible = [];   // 커밋 스코프는 필터 UI가 없으니 전체를 순회 대상으로(visibleList 폴백).
        fillCommitBanner(p.hash, p.subject);
        setIssueScope('commit');
        const list = document.getElementById('is-list');
        const listEmpty = document.getElementById('is-list-empty');
        list.innerHTML = '';
        if (!isDocs.length) {
            listEmpty.classList.remove('hidden');
            listEmpty.innerHTML = decorate(p.empty || '이 커밋과 연관된 이슈가 없습니다.');
        } else {
            listEmpty.classList.add('hidden');
            isDocs.forEach((d, i) => list.appendChild(renderIssueItem(d, i)));
        }
        showIssueList();
        isShow('body');
    }
    function showIssueList() {
        document.getElementById('is-list-view').classList.remove('hidden');
        document.getElementById('is-detail-view').classList.add('hidden');
        document.getElementById('is-chat-view').classList.add('hidden');
    }
    function showIssueDetail() {
        document.getElementById('is-list-view').classList.add('hidden');
        document.getElementById('is-detail-view').classList.remove('hidden');
        document.getElementById('is-chat-view').classList.add('hidden');
    }

    // ── 이슈 AI 챗봇 ────────────────────────────────────────────────────
    let chatDoc = null;          // 챗봇이 학습한 이슈(isDocs[isIndex] 스냅샷)
    let chatMessages = [];       // 멀티턴 히스토리 [{role, content}]
    let chatStreaming = false;   // 응답 수신 중
    let chatBotEl = null;        // 스트리밍 중인 봇 말풍선 요소
    let chatBotText = '';        // 현재 봇 답변 누적 텍스트

    function showIssueChat() {
        document.getElementById('is-list-view').classList.add('hidden');
        document.getElementById('is-detail-view').classList.add('hidden');
        document.getElementById('is-chat-view').classList.remove('hidden');
    }
    function openIssueChat() {
        chatDoc = isDocs[isIndex] || null;
        if (!chatDoc) { return; }
        chatMessages = []; chatStreaming = false; chatBotEl = null; chatBotText = '';
        document.getElementById('ic-num').textContent = (chatDoc.issueNumber != null) ? ('#' + chatDoc.issueNumber) : '';
        document.getElementById('ic-name').textContent = chatDoc.title || '(제목 없음)';
        const comments = chatDoc.comments || [];
        const commentCount = comments.filter(c => c && c.kind === 'comment').length;
        const linkedCount = comments.filter(c => c && (c.event === 'committed' || c.event === 'referenced')).length;
        // 첨부 수 — 본문 + 코멘트 첨부를 URL 기준 중복 제거(백엔드 collect_attachments 와 동일 기준).
        const attUrls = new Set();
        (chatDoc.attachments || []).forEach(a => { if (a && a.url) { attUrls.add(a.url); } });
        comments.forEach(c => ((c && c.attachments) || []).forEach(a => { if (a && a.url) { attUrls.add(a.url); } }));
        const attCount = attUrls.size;
        document.getElementById('ic-learned').textContent =
            '✦ 커밋 ' + linkedCount + ' · 코멘트 ' + commentCount + ' · 첨부 ' + attCount + ' 학습 완료';
        const feed = document.getElementById('ic-feed');
        feed.innerHTML = '';
        appendChatBubble('bot', '이 이슈에 대해 무엇이든 물어보세요. 본문·댓글·첨부 문서·연관 커밋을 근거로 답합니다.');
        setChatSending(false);
        const t = document.getElementById('ic-text');
        t.value = '';
        showIssueChat();
        setTimeout(() => t.focus(), 0);
    }
    function appendChatBubble(role, text) {
        const feed = document.getElementById('ic-feed');
        const row = document.createElement('div');
        row.className = 'ic-msg ' + (role === 'user' ? 'user' : 'bot');
        const bubble = document.createElement('div');
        bubble.className = 'ic-bubble';
        // 사용자 입력은 평문, 봇 답변은 마크다운으로 렌더해 가독성을 높인다.
        if (role === 'user') { bubble.textContent = text || ''; }
        else { bubble.innerHTML = renderChatMarkdown(text || ''); }
        row.appendChild(bubble);
        feed.appendChild(row);
        feed.scrollTop = feed.scrollHeight;
        return bubble;
    }
    // ── 답변 마크다운 → HTML (제목/굵게/목록/인용/구분선/코드) ──────────
    function chatEsc(s) {
        return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    }
    function chatInline(t) {
        return chatEsc(t)
            .replace(/\`([^\`]+)\`/g, '<code>$1</code>')
            .replace(/\\*\\*([^*]+)\\*\\*/g, '<strong>$1</strong>')
            .replace(/(^|[^*])\\*([^*\\s][^*]*?)\\*(?!\\*)/g, '$1<em>$2</em>');
    }
    function renderChatMarkdown(src) {
        const lines = String(src || '').split(/\\r?\\n/);
        let html = '', inQuote = false;
        const stack = [];   // 들여쓰기 기준 리스트 스택 {tag, indent} — 번호 매김 유지 + 하위 불릿 중첩
        const closeQuote = () => { if (inQuote) { html += '</blockquote>'; inQuote = false; } };
        const closeAll = () => { while (stack.length) { html += '</' + stack.pop().tag + '>'; } };
        for (const raw of lines) {
            const line = raw.replace(/\\s+$/, '');
            let m;
            if (/^\\s*([-*_])\\1{2,}\\s*$/.test(line)) { closeAll(); closeQuote(); html += '<hr/>'; continue; }
            if ((m = line.match(/^\\s*(#{1,4})\\s+(.*)$/))) { closeAll(); closeQuote(); const lv = m[1].length; html += '<h' + lv + '>' + chatInline(m[2]) + '</h' + lv + '>'; continue; }
            if ((m = line.match(/^\\s*>\\s?(.*)$/))) { closeAll(); if (!inQuote) { html += '<blockquote>'; inQuote = true; } html += chatInline(m[1]) + '<br/>'; continue; }
            const om = line.match(/^(\\s*)\\d+\\.\\s+(.*)$/);
            const um = om ? null : line.match(/^(\\s*)[-*]\\s+(.*)$/);
            if (om || um) {
                closeQuote();
                const lead = (om ? om[1] : um[1]).replace(/\\t/g, '    ').length;
                const tag = om ? 'ol' : 'ul';
                const content = om ? om[2] : um[2];
                while (stack.length && stack[stack.length - 1].indent > lead) { html += '</' + stack.pop().tag + '>'; }
                const top = stack[stack.length - 1];
                if (!top || top.indent < lead) { html += '<' + tag + '>'; stack.push({ tag, indent: lead }); }
                else if (top.indent === lead && top.tag !== tag) { html += '</' + stack.pop().tag + '>'; html += '<' + tag + '>'; stack.push({ tag, indent: lead }); }
                html += '<li>' + chatInline(content) + '</li>';
                continue;
            }
            if (line.trim() === '') { continue; }   // 빈 줄: 리스트는 유지(느슨하게)
            closeAll(); closeQuote();
            html += '<p>' + chatInline(line) + '</p>';
        }
        closeAll(); closeQuote();
        return html;
    }
    function setChatSending(on) {
        chatStreaming = on;
        const btn = document.getElementById('ic-send');
        if (btn) { btn.disabled = on; }
    }
    function chatIssuePayload() {
        const d = chatDoc || {};
        return {
            issueNumber: d.issueNumber != null ? d.issueNumber : null,
            title: d.title || '', body: d.body || '', state: d.state || '',
            labels: d.labels || [], assignee: d.assignee || '', url: d.url || '',
            attachments: d.attachments || [], comments: d.comments || [],
        };
    }
    function sendChat() {
        if (chatStreaming || !chatDoc) { return; }
        const t = document.getElementById('ic-text');
        const q = (t.value || '').trim();
        if (!q) { return; }
        chatMessages.push({ role: 'user', content: q });
        appendChatBubble('user', q);
        t.value = '';
        chatBotText = '';
        chatBotEl = appendChatBubble('bot', '');
        chatBotEl.innerHTML = '<span class="spinner"></span>';
        setChatSending(true);
        vscode.postMessage({ type: 'issueChatAsk', payload: { issue: chatIssuePayload(), messages: chatMessages.slice() } });
    }
    function onChatFrame(p) {
        if (!p) { return; }
        const feed = document.getElementById('ic-feed');
        if (p.kind === 'delta') {
            if (!chatBotEl) { chatBotEl = appendChatBubble('bot', ''); }
            chatBotText += (p.text || '');
            chatBotEl.innerHTML = renderChatMarkdown(chatBotText);
            if (feed) { feed.scrollTop = feed.scrollHeight; }
        } else if (p.kind === 'done') {
            if (chatBotEl) { chatMessages.push({ role: 'assistant', content: chatBotText || chatBotEl.textContent || '' }); }
            chatBotText = ''; chatBotEl = null; setChatSending(false);
        } else if (p.kind === 'error') {
            if (!chatBotEl) { chatBotEl = appendChatBubble('bot', ''); }
            chatBotEl.classList.add('error');
            chatBotEl.textContent = '답변을 가져오지 못했습니다: ' + (p.text || '알 수 없는 오류');
            chatBotText = ''; chatBotEl = null; setChatSending(false);
        }
    }
    // 입력창: Enter 전송 / Shift+Enter 줄바꿈 (높이는 CSS 로 3줄 고정, 초과 시 스크롤)
    (function bindChatInput() {
        const t = document.getElementById('ic-text');
        if (!t) { return; }
        t.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendChat(); }
        });
    })();
    function renderIssueList() {
        const list = document.getElementById('is-list');
        list.innerHTML = '';

        // 검색어(제목/번호/라벨) 일치 여부 — 카운트와 목록이 같은 기준을 쓰게 헬퍼로 묶는다.
        const q = isQuery.trim().toLowerCase();
        const matchesQuery = (d) => {
            if (!q) { return true; }
            const hay = [d.title || '', d.issueNumber != null ? ('#' + d.issueNumber) : '', (d.labels || []).join(' ')]
                .join(' ').toLowerCase();
            return hay.indexOf(q) !== -1;
        };

        // 상태별 건수 — 필터 탭 배지 갱신. 검색어를 통과한 항목만 세어 탭 숫자와 실제 목록 건수를 일치시킨다.
        const counts = { all: 0, open: 0, closed: 0 };
        isDocs.forEach(d => { if (matchesQuery(d)) { counts.all++; counts[issueBucket(d)]++; } });
        document.querySelectorAll('#is-filters .is-filter__n').forEach(n => {
            n.textContent = counts[n.dataset.count] != null ? counts[n.dataset.count] : 0;
        });
        document.querySelectorAll('#is-filters .is-filter').forEach(b => {
            b.classList.toggle('active', b.dataset.filter === isFilter);
        });

        // 검색어 + 상태 필터로 거른다. 원본 인덱스를 유지해 상세 이동이 어긋나지 않게 한다.
        // 보이는 항목의 원본 인덱스를 isVisible 에 같은 순서로 쌓아, 상세 이전/다음이 이 부분집합만 돌게 한다.
        isVisible = [];
        isDocs.forEach((d, i) => {
            if (isFilter !== 'all' && issueBucket(d) !== isFilter) { return; }
            if (!matchesQuery(d)) { return; }
            list.appendChild(renderIssueItem(d, i));
            isVisible.push(i);
        });
        const shown = isVisible.length;
        // 커밋-스코프 빈 결과가 덮어썼을 수 있으니 파일 검색 기본 문구로 복원한다.
        const listEmpty = document.getElementById('is-list-empty');
        listEmpty.textContent = '검색 결과가 없습니다.';
        listEmpty.classList.toggle('hidden', shown > 0);
    }
    // 상태 버킷 → 표시 라벨/클래스 (열림/닫힘).
    const IS_STATE = {
        open:   { label: '열림', cls: 'open' },
        closed: { label: '닫힘', cls: 'closed' },
    };
    // 담당자 아바타 배경색 — 이름을 해시해 안정적으로 같은 색을 준다.
    const AVA_COLORS = ['#E05454', '#2CB8B8', '#8B5CF6', '#D97706', '#16A34A', '#3B82F6', '#EC4899', '#F97316'];
    function avatarColor(name) {
        let h = 0;
        for (let k = 0; k < name.length; k++) { h = (h * 31 + name.charCodeAt(k)) >>> 0; }
        return AVA_COLORS[h % AVA_COLORS.length];
    }
    function makeAvatar(name) {
        const ava = document.createElement('span');
        ava.className = 'is-avatar';
        ava.style.background = avatarColor(name);
        ava.textContent = name.charAt(0);
        ava.title = name;
        return ava;
    }

    function renderIssueItem(d, i) {
        const bucket = issueBucket(d);
        const st = IS_STATE[bucket] || IS_STATE.open;

        const el = document.createElement('div');
        el.className = 'is-item';
        el.dataset.action = 'openIssueDetail';   // 항목 선택 → 상세 화면(외부 열기 아님)
        el.dataset.index = i;

        // 머리: 상태 · 번호
        const head = document.createElement('div');
        head.className = 'is-item__head';
        head.innerHTML =
            '<span class="is-item__state ' + st.cls + '"></span>' +
            '<span class="is-item__num"></span>' +
            '<span class="is-item__date"></span>';
        head.querySelector('.is-item__state').textContent = st.label;
        head.querySelector('.is-item__num').textContent = (d.issueNumber != null) ? ('#' + d.issueNumber) : '';
        // 등록일(개설일) — "M월 D일". 백엔드 미전송 시 빈칸.
        head.querySelector('.is-item__date').textContent = d.createdAt ? isMonthDay(d.createdAt) : '';
        el.appendChild(head);

        // 제목(최대 2줄)
        const title = document.createElement('div');
        title.className = 'is-item__title';
        title.textContent = d.title || '(제목 없음)';
        el.appendChild(title);

        // 하단: 라벨 칩 ……… 담당자 아바타 · 💬 코멘트 · 📎 첨부
        const labels = d.labels || [];
        const attCount = (d.attachments || []).length;
        const bottom = document.createElement('div');
        bottom.className = 'is-item__bottom';
        const labelWrap = document.createElement('span');
        labelWrap.className = 'is-item__labels';
        labels.slice(0, 3).forEach(name => {
            const chip = document.createElement('span');
            chip.className = 'is-item__label';
            chip.textContent = (String(name).charAt(0) === '#' ? '' : '#') + name;
            labelWrap.appendChild(chip);
        });
        bottom.appendChild(labelWrap);

        const right = document.createElement('span');
        right.className = 'is-item__metaright';
        if (d.assignee) { right.appendChild(makeAvatar(d.assignee)); }
        if (d.commentCount) {
            const c = document.createElement('span');
            c.innerHTML = ICON.comment; c.appendChild(document.createTextNode(String(d.commentCount)));
            right.appendChild(c);
        }
        if (attCount) {
            const a = document.createElement('span');
            a.innerHTML = ICON.clip; a.appendChild(document.createTextNode(String(attCount)));
            right.appendChild(a);
        }
        bottom.appendChild(right);
        el.appendChild(bottom);
        return el;
    }

    // ── 이슈 상세 화면 ───────────────────────────────────────────
    function isDateOnly(s) {
        if (!s) { return ''; }
        const m = String(s).slice(0, 10);
        return /^\\d{4}-\\d{2}-\\d{2}$/.test(m) ? m : String(s);
    }
    // 느슨한 날짜 파싱 — ISO8601 / "YYYY-MM-DD" / 유닉스초. 실패 시 null.
    function isParseDate(s) {
        if (!s) { return null; }
        if (/^\\d{9,11}$/.test(String(s))) { return new Date(Number(s) * 1000); }
        const d = new Date(s);
        return isNaN(d.getTime()) ? null : d;
    }
    // 코멘트/이벤트 날짜 — "M월 D일". 파싱 실패 시 원본 앞 10자.
    function isMonthDay(s) {
        const d = isParseDate(s);
        return d ? ((d.getMonth() + 1) + '월 ' + d.getDate() + '일') : isDateOnly(s);
    }
    // 활동 피드 날짜+시각 — "YYYY년 M월 D일 오전/오후 h:mm" (로컬 시간). 실패 시 isMonthDay.
    function isDateTime(s) {
        const d = isParseDate(s);
        if (!d) { return isMonthDay(s); }
        let h = d.getHours();
        const ampm = h < 12 ? '오전' : '오후';
        h = h % 12; if (h === 0) { h = 12; }
        const mm = String(d.getMinutes()).padStart(2, '0');
        return d.getFullYear() + '년 ' + (d.getMonth() + 1) + '월 ' + d.getDate() + '일 ' + ampm + ' ' + h + ':' + mm;
    }
    // 메타 '업데이트' 칸 — "방금 / N분 전 / N시간 전 …" 상대 시각. 실패 시 isDateOnly.
    function isRelative(s) {
        const d = isParseDate(s);
        if (!d) { return isDateOnly(s); }
        const sec = Math.floor((Date.now() - d.getTime()) / 1000);
        if (sec < 60) { return '방금'; }
        const min = Math.floor(sec / 60);
        if (min < 60) { return min + '분 전'; }
        const hour = Math.floor(min / 60);
        if (hour < 24) { return hour + '시간 전'; }
        const day = Math.floor(hour / 24);
        if (day < 7) { return day + '일 전'; }
        const week = Math.floor(day / 7);
        if (week < 5) { return week + '주 전'; }
        const month = Math.floor(day / 30);
        if (month < 12) { return month + '개월 전'; }
        return Math.floor(day / 365) + '년 전';
    }
    // 한글 조사 — 받침 있으면 withFinal, 없으면 withoutFinal. (시스템 이벤트 문장 조립용)
    function isJosa(word, withFinal, withoutFinal) {
        const ch = String(word || '').trim().slice(-1);
        if (!ch) { return withoutFinal; }
        const code = ch.charCodeAt(0);
        if (code < 0xac00 || code > 0xd7a3) { return withoutFinal; }
        return (code - 0xac00) % 28 !== 0 ? withFinal : withoutFinal;
    }
    function openIssueDetail(i) {
        if (!isDocs.length) { return; }
        isIndex = Math.max(0, Math.min(i, isDocs.length - 1));
        renderIssueDetail();
        showIssueDetail();
    }

    // 상세 이전/다음 이동 — '보이는 목록'(visibleList: 필터+검색이 적용된 부분집합) 안에서만 움직인다.
    // delta: -1(이전) | +1(다음). 현재 항목(isIndex)의 보이는-목록 내 위치를 찾아 delta 만큼 옮긴 뒤,
    // 그 위치의 원본 인덱스로 openIssueDetail 을 호출한다(버그 #4).
    function stepIssue(delta) {
        const vis = visibleList();                       // 화면에 보이는 항목의 원본 인덱스들(순서 보존)
        if (!vis.length) { return; }
        const pos = vis.indexOf(isIndex);                // 현재 항목이 그 안에서 몇 번째 칸인가(없으면 -1)
        // 필터에 안 걸린 항목을 보고 있으면(pos === -1) 보이는 목록의 첫 칸으로 진입.
        const next = pos === -1 ? 0 : pos + delta;
        if (next < 0 || next >= vis.length) { return; }  // 양 끝을 벗어나면 멈춤(clamp) — 페이저 disabled 와 일치.
        openIssueDetail(vis[next]);
    }
    function appendMetaRow(dl, term, value, strong) {
        const dt = document.createElement('dt'); dt.textContent = term;
        const dd = document.createElement('dd');
        if (strong) { const st = document.createElement('strong'); st.textContent = value; dd.appendChild(st); }
        else { dd.textContent = value; }
        dl.appendChild(dt); dl.appendChild(dd);
    }
    // 첨부가 이미지인지 — 확장자(.png 등) 또는 GitHub 드래그-드롭 업로드(user-attachments/assets,
    // 확장자 없음)로 판별. assets 는 이미지가 아닐 수도 있어, 일단 미리보기로 시도하고 로드 실패 시 칩으로 폴백한다.
    function isImageAttachment(a) {
        const s = ((a && a.url) || '') + ' ' + ((a && a.label) || '');
        const low = s.toLowerCase();
        if (/\\.(png|jpe?g|gif|webp|svg|bmp|avif)(\\?|#|$)/.test(low)) { return true; }
        if (/user-attachments\\/assets\\//.test(low)) { return true; }
        return false;
    }
    // 비-이미지 첨부 — 확장자 배지가 달린 파일 칩.
    function renderFileChip(a) {
        const el = document.createElement('div');
        el.className = 'is-d-att';
        el.dataset.action = 'openIssue';        // 첨부는 직접 링크를 외부로 연다
        el.dataset.url = cleanUrl(a.url);
        const ext = (String(a.label || '').split('.').pop() || '').toUpperCase().slice(0, 4);
        el.innerHTML =
            '<span class="is-d-att__ico"></span>' +
            '<div style="min-width:0">' +
                '<div class="is-d-att__name"></div>' +
                (a.pageCount ? '<div class="is-d-att__meta"></div>' : '') +
            '</div>';
        el.querySelector('.is-d-att__ico').textContent = ext && ext !== (a.label || '') ? ext : 'FILE';
        el.querySelector('.is-d-att__name').textContent = a.label || a.url || '첨부';
        if (a.pageCount) { el.querySelector('.is-d-att__meta').textContent = a.pageCount + ' p'; }
        return el;
    }
    // 이미지 첨부 — 인라인 미리보기. 클릭 시 원본을 외부로 연다.
    // CSP 는 img-src 에 https: 를 허용한다(renderHtml 참고). 로드 실패(권한 만료·삭제 등) 시 파일 칩으로 교체.
    function renderImageAttachment(a) {
        const url = cleanUrl(a.url);
        const fig = document.createElement('figure');
        fig.className = 'is-d-img';
        fig.dataset.action = 'zoomImage';   // 클릭 시 확대 팝업(라이트박스)
        fig.dataset.url = url;
        const img = document.createElement('img');
        img.src = url;
        img.alt = a.label || '첨부 이미지';
        img.loading = 'lazy';
        img.addEventListener('error', () => { fig.replaceWith(renderFileChip(a)); });
        fig.appendChild(img);
        if (a.label) {
            const cap = document.createElement('figcaption');
            cap.textContent = a.label;
            fig.appendChild(cap);
        }
        return fig;
    }
    function renderAttachment(a) {
        return (a && a.url && isImageAttachment(a)) ? renderImageAttachment(a) : renderFileChip(a);
    }
    function renderIssueDetail() {
        const d = isDocs[isIndex];
        const wrap = document.getElementById('is-detail-view');
        wrap.innerHTML = '';
        if (!d) { return; }

        const bucket = issueBucket(d);
        const stateCls = bucket;
        const stateLabel = (IS_STATE[bucket] || IS_STATE.open).label;

        // 네비게이션 (목록으로 / n·총건수)
        const nav = document.createElement('div');
        nav.className = 'is-d-nav';
        nav.innerHTML =
            '<button class="is-d-back" data-action="issueBack">‹ 목록으로</button>' +
            '<span class="is-d-pager">' +
                '<button data-action="issuePrev">‹</button>' +
                '<span class="pos"></span>' +
                '<button data-action="issueNext">›</button>' +
            '</span>';
        // 페이저는 '보이는 목록' 기준 — 필터로 가려진 항목은 총건수/위치에서 빠진다(버그 #4).
        const vis = visibleList();
        const pos = vis.indexOf(isIndex);
        nav.querySelector('.pos').textContent = ((pos === -1 ? 0 : pos) + 1) + ' / ' + vis.length;
        nav.querySelector('[data-action="issuePrev"]').disabled = pos <= 0;
        nav.querySelector('[data-action="issueNext"]').disabled = pos === -1 || pos >= vis.length - 1;
        wrap.appendChild(nav);

        // 헤더: 상태 배지 + 이슈 번호 + AI 질문
        const head = document.createElement('div');
        head.className = 'is-d-head';
        head.innerHTML =
            '<span class="is-d-idline">' +
                '<span class="is-d-state ' + stateCls + '"></span>' +
                '<span class="is-d-num"></span>' +
            '</span>' +
            '<button class="is-d-ai" data-action="issueAiAsk">✦ AI 질문</button>';
        head.querySelector('.is-d-state').textContent = stateLabel;
        head.querySelector('.is-d-num').textContent = (d.issueNumber != null) ? ('#' + d.issueNumber) : '';
        wrap.appendChild(head);

        // 제목 (클릭 시 원문 이슈 열기)
        const title = document.createElement('div');
        title.className = 'is-d-title';
        if (d.url) { title.dataset.action = 'openIssue'; title.dataset.url = d.url; title.title = '원문 이슈 열기'; }
        title.textContent = d.title || '(제목 없음)';
        wrap.appendChild(title);

        // 라벨 칩 (매치 신뢰도 배지는 노출하지 않는다)
        const labelWrap = document.createElement('div');
        labelWrap.className = 'is-d-labels';
        (d.labels || []).forEach(name => {
            const chip = document.createElement('span');
            chip.className = 'is-d-label';
            chip.textContent = (String(name).charAt(0) === '#' ? '' : '#') + name;
            labelWrap.appendChild(chip);
        });
        if (labelWrap.childNodes.length) { wrap.appendChild(labelWrap); }

        // 메타 (담당자 / 개설 / 업데이트) — '연결된 코드' 행은 노출하지 않는다.
        const meta = document.createElement('dl');
        meta.className = 'is-d-meta';
        const assignee = d.assignee || '';
        // 담당자: 아바타 + 이름 (미지정이면 평문). appendMetaRow 와 달리 아바타를 끼운다.
        const dtA = document.createElement('dt'); dtA.textContent = '담당자';
        const ddA = document.createElement('dd');
        if (assignee) {
            ddA.appendChild(makeAvatar(assignee));
            const nm = document.createElement('strong'); nm.textContent = assignee; ddA.appendChild(nm);
        } else { ddA.textContent = '미지정'; }
        meta.appendChild(dtA); meta.appendChild(ddA);
        const created = isDateOnly(d.createdAt);
        const updated = isRelative(d.updatedAt);
        if (created) { appendMetaRow(meta, '개설', created, false); }
        if (updated) { appendMetaRow(meta, '업데이트', updated, false); }
        wrap.appendChild(meta);

        // 본문 — 인용(> …)은 인용 박스, 이미지(<img>/![](url))는 인라인 미리보기, 나머지는 텍스트.
        const bodyImgUrls = [];
        if (d.body) {
            const body = document.createElement('div');
            body.className = 'is-d-body';
            body.innerHTML = renderIssueBodyHTML(d.body, bodyImgUrls);
            // 인라인 이미지 로드 실패(비공개 레포·만료·삭제) 시 '열기' 링크로 폴백.
            body.querySelectorAll('img.is-d-bimg').forEach(img => {
                img.addEventListener('error', () => {
                    const link = document.createElement('span');
                    link.className = 'is-d-bimg-fail';
                    link.dataset.action = 'openIssue';
                    link.dataset.url = img.dataset.url || '';
                    link.textContent = '이미지 열기 ↗';
                    img.replaceWith(link);
                });
            });
            wrap.appendChild(body);
        }

        // 첨부파일 — 본문에 이미 인라인으로 들어간 이미지는 중복 노출하지 않는다.
        const atts = (d.attachments || []).filter(a => bodyImgUrls.indexOf(cleanUrl(a.url)) === -1);
        if (atts.length) {
            const sec = document.createElement('div');
            sec.className = 'is-d-sec-title';
            sec.innerHTML = ICON.clip; sec.appendChild(document.createTextNode(' 첨부파일 ' + atts.length));
            wrap.appendChild(sec);
            const list = document.createElement('div');
            list.className = 'is-d-atts';
            atts.forEach(a => list.appendChild(renderAttachment(a)));
            wrap.appendChild(list);
        }

        // 활동 타임라인 (코멘트 + 시스템 이벤트)
        renderComments(wrap, d.comments || []);
    }

    // ── 활동 타임라인 (코멘트 + 시스템 이벤트) ─────────────────────────
    // 시간순 list 를 훑으며: 사람 코멘트는 카드로, 같은 행위자의 연속 '메타' 이벤트
    // (라벨/담당자/열고닫기)는 한 문장으로 묶어 한 줄로, 커밋/참조/노트는 단독 줄로 그린다.
    const IS_META_EVENTS = { labeled: 1, assigned: 1, closed: 1, reopened: 1 };
    function renderComments(wrap, list) {
        if (!list || !list.length) { return; }
        const sec = document.createElement('div');
        sec.className = 'is-d-sec-title';
        sec.innerHTML = ICON.comment; sec.appendChild(document.createTextNode(' 활동 ' + list.length));
        wrap.appendChild(sec);

        const feed = document.createElement('div');
        feed.className = 'is-d-feed';
        let i = 0;
        while (i < list.length) {
            const it = list[i];
            if (it.kind === 'comment') { feed.appendChild(renderCommentCard(it)); i++; continue; }
            if (IS_META_EVENTS[it.event]) {
                // 같은 행위자의 연속 메타 이벤트를 한 묶음으로 모은다.
                const group = [it];
                let j = i + 1;
                while (j < list.length && list[j].kind === 'event' && IS_META_EVENTS[list[j].event]
                       && (list[j].author || '') === (it.author || '')) {
                    group.push(list[j]); j++;
                }
                feed.appendChild(renderEventLine(it.author, describeEventGroup(group), it.createdAt));
                i = j;
                continue;
            }
            // 커밋/참조/노트 — 단독 줄.
            if (it.event === 'committed' || it.event === 'referenced') {
                feed.appendChild(renderCommitLine(it));
            } else {
                feed.appendChild(renderEventLine(it.author, (it.body || '').trim(), it.createdAt));
            }
            i++;
        }
        wrap.appendChild(feed);
    }

    // 묶인 메타 이벤트 → 한국어 한 문장. 예: "spec, P1 라벨을 추가하고 홍길동님을 담당자로 지정"
    // (행위자 "…님이" 접두는 renderEventLine 이 붙인다.)
    function describeEventGroup(group) {
        const labels = group.filter(e => e.event === 'labeled').map(e => e.label).filter(Boolean);
        const assignees = group.filter(e => e.event === 'assigned').map(e => e.assignee).filter(Boolean);
        const phrases = [];
        if (labels.length) { phrases.push(labels.join(', ') + ' 라벨을 추가'); }
        if (assignees.length) { phrases.push(assignees.join(', ') + isJosa(assignees[assignees.length - 1], '을', '를') + ' 담당자로 지정'); }
        if (group.some(e => e.event === 'closed')) { phrases.push('이슈를 닫음'); }
        if (group.some(e => e.event === 'reopened')) { phrases.push('이슈를 다시 엶'); }
        // 마지막 구절만 종결형, 앞 구절은 "…하고" 로 잇는다.
        return phrases.map((p, k) => k < phrases.length - 1 ? p + '하고' : p).join(' ');
    }

    // 시스템 이벤트 한 줄 — 작은 점 + "{행위자}님이 {설명} · {날짜}".
    function renderEventLine(author, text, date) {
        const el = document.createElement('div');
        el.className = 'is-d-ev';
        el.innerHTML = '<span class="is-d-ev__dot"></span><span class="is-d-ev__txt"></span>';
        const txt = el.querySelector('.is-d-ev__txt');
        if (author) { const b = document.createElement('b'); b.textContent = author; txt.appendChild(b);
            txt.appendChild(document.createTextNode('님이 ')); }
        txt.appendChild(document.createTextNode(text || ''));
        const dt = document.createElement('span'); dt.className = 'is-d-ev__date';
        dt.textContent = ' · ' + isDateTime(date); txt.appendChild(dt);
        return el;
    }

    // 커밋/참조 이벤트 한 줄 — "{행위자}님이 {sha} — {커밋요약} · {날짜}".
    function renderCommitLine(it) {
        const el = document.createElement('div');
        el.className = 'is-d-ev';
        el.innerHTML = '<span class="is-d-ev__dot"></span><span class="is-d-ev__txt"></span>';
        const txt = el.querySelector('.is-d-ev__txt');
        if (it.author) { const b = document.createElement('b'); b.textContent = it.author; txt.appendChild(b);
            txt.appendChild(document.createTextNode('님이 ')); }
        const sha = (it.commitSha || '').slice(0, 6);
        if (sha) { const c = document.createElement('code'); c.textContent = sha; txt.appendChild(c); }
        if (it.commitSummary) { txt.appendChild(document.createTextNode(' — ' + it.commitSummary)); }
        const dt = document.createElement('span'); dt.className = 'is-d-ev__date';
        dt.textContent = ' · ' + isDateTime(it.createdAt); txt.appendChild(dt);
        return el;
    }

    // 사람 코멘트 카드 — 아바타 + 이름 + 날짜 / 본문 / 첨부.
    function renderCommentCard(c) {
        const author = c.author || '익명';
        const el = document.createElement('div');
        el.className = 'is-cmt';
        const head = document.createElement('div');
        head.className = 'is-cmt__head';
        head.appendChild(makeAvatar(author));
        const nm = document.createElement('span'); nm.className = 'is-cmt__name'; nm.textContent = author;
        head.appendChild(nm);
        const dt = document.createElement('span'); dt.className = 'is-cmt__date'; dt.textContent = isDateTime(c.createdAt);
        head.appendChild(dt);
        el.appendChild(head);
        if (c.body) {
            const body = document.createElement('div');
            body.className = 'is-cmt__body';
            body.innerHTML = renderBold(c.body);
            el.appendChild(body);
        }
        (c.attachments || []).forEach(a => el.appendChild(renderAttachment(a)));
        return el;
    }

    // 커밋 이력이 없는 라인(미커밋 파일 등) — 깨져 보이는 메타 카드 대신 안내 문구만 깔끔히
    function showInfo(message) {
        document.getElementById('empty').classList.add('hidden');
        document.getElementById('content').classList.add('hidden');
        const info = document.getElementById('info');
        info.classList.remove('hidden');
        document.getElementById('info-text').innerHTML = decorate(message);
        revealTabs(true);
    }

    // 환영 화면에선 탭 바를 숨기고, 분석을 시작하면 드러낸다.
    function revealTabs(on) {
        document.querySelector('.tabs').classList.toggle('hidden', !on);
    }

    // ─────────────────── 블레임 스트리밍 렌더 ───────────────────
    // 타임라인 tlStreaming/tlDelta/tlResult 와 동일한 3단 흐름.
    // blStreaming: meta 로 메타표·이력을 즉시 그리고 콜아웃에 캐럿을 띄운다.
    // blDelta    : 설명 토큰을 콜아웃(#ca-exp)에 이어 붙인다.
    // blResult   : 캐럿 제거 + 핵심 한 줄 강조 + 접기 + 칩 확정.
    let blExp = '';

    // 설명을 '핵심 한 줄(lead)'과 '자세한 배경(rest)'으로 가른다.
    // 백엔드 headline 이 있으면 그것을 핵심으로, 없으면 첫 문장(…다./…요./. ! ?)을 경계로 본다.
    function splitLead(text, headline) {
        const t = String(text || '').trim();
        const h = String(headline || '').trim();
        if (h) {
            // 본문이 핵심 문장으로 시작하면 그 뒤만 배경으로(중복 방지), 아니면 본문 전체를 배경으로.
            if (t && t.indexOf(h) === 0) {
                return { lead: h, rest: t.slice(h.length).replace(/^[\\s,]+/, '').trim() };
            }
            return { lead: h, rest: t };
        }
        if (!t) { return { lead: '', rest: '' }; }
        // 헤드라인이 없을 때의 폴백 — 첫 문장 끝까지를 핵심으로.
        let m = t.match(/^[\\s\\S]*?(?:다\\.|요\\.|[.!?])/);
        // 한 문장이 너무 길거나(런온) 종결부호가 없으면 첫 쉼표(절 경계)까지를 핵심으로 잡는다.
        if (!m || m[0].length > 45) {
            const c = t.match(/^[\\s\\S]{10,60}?,/);
            if (c) { m = c; }
        }
        if (m) {
            return {
                lead: m[0].replace(/[\\s,]+$/, ''),
                rest: t.slice(m[0].length).replace(/^[\\s,]+/, '').trim(),
            };
        }
        return { lead: t, rest: '' };
    }

    // 작성자 이름 → 아바타 이니셜. "yejin-kb" → "YK", "홍길동" → "홍".
    function initials(name) {
        const parts = String(name || '').split(/[^A-Za-z0-9가-힣]+/).filter(Boolean);
        if (!parts.length) { return '?'; }
        const a = parts[0].charAt(0);
        const b = parts.length > 1 ? parts[1].charAt(0) : '';
        return (a + b).toUpperCase();
    }

    // "2026-06-07" → "2026년 6월 7일". 파싱 실패 시 원본 그대로.
    function longDateKo(iso) {
        const m = String(iso || '').match(/(\\d{4})-(\\d{1,2})-(\\d{1,2})/);
        if (!m) { return iso || ''; }
        return m[1] + '년 ' + Number(m[2]) + '월 ' + Number(m[3]) + '일';
    }

    // 작성자 행(아바타 + 이름 + 날짜)을 채운다. 메타(스트리밍)·결과(캐시) 양쪽에서 호출.
    function fillAuthor(p) {
        const av = document.getElementById('ca-avatar');
        const nm = document.getElementById('ca-name');
        const wh = document.getElementById('ca-when');
        if (av) { av.textContent = initials(p.author); }
        if (nm) { nm.textContent = p.author || '?'; }
        if (wh) {
            const d = longDateKo(p.dateFull);
            wh.textContent = d + (p.relative ? ' · ' + p.relative + ' 수정' : '');
        }
    }

    // 콜아웃 본문을 핵심 한 줄(lead)과 자세한 배경(detail)으로 갈라 채운다(요소 기반).
    // 블레임·타임라인이 같은 .callout 구조를 공유하므로 ID 만 달리해 재사용한다.
    function fillCalloutEls(ids, text, headline, fallback) {
        fillCalloutSplit(ids, splitLead(text, headline), fallback, text);
    }
    // 이미 갈라진 {lead, rest} 를 콜아웃 요소에 채운다 — '어떻게 가를지'(split)와 '어디에 그릴지'(DOM)를 분리.
    function fillCalloutSplit(ids, split, fallback, rawText) {
        const leadEl = document.getElementById(ids.lead);
        leadEl.innerHTML = renderBold(split.lead || rawText || fallback);
        const sec = document.getElementById(ids.detailSec);
        const detail = document.getElementById(ids.detail);
        const more = document.getElementById(ids.more);
        if (split.rest) {
            detail.innerHTML = renderBold(split.rest);
            sec.classList.remove('hidden');
            if (more) { more.classList.remove('hidden'); more.classList.add('expanded'); more.textContent = '접기'; }
        } else {
            detail.innerHTML = '';
            sec.classList.add('hidden');
            if (more) { more.classList.add('hidden'); }
        }
    }
    // 스트리밍 중(아직 안 끝난) raw JSON 에서 summary 값만 뽑아낸다.
    //   raw 예(미완성 가능): '{"summary": "제목\\n상세 일부…'  ← 닫는 따옴표가 아직 없음
    //   값의 시작/끝 '경계'만 우리가 찾고, 이스케이프(\\n \\" \\\\ \\t \\uXXXX) 복원은
    //   JSON.parse 에 통째로 맡긴다 — 손으로 풀지 않는다.
    function tlExtractStreamingSummary(raw) {
        const s = String(raw || '');
        const key = s.indexOf('"summary"');
        if (key < 0) { return ''; }
        const open = s.indexOf('"', key + 9);   // 값 여는 따옴표
        if (open < 0) { return ''; }
        // 값 닫는 따옴표 찾기 — 백슬래시로 이스케이프된 따옴표(\\")는 끝으로 치지 않는다.
        let end = -1;
        for (let i = open + 1; i < s.length; i++) {
            if (s[i] === '\\\\') { i++; continue; }   // 이스케이프된 다음 글자는 통째로 건너뜀
            if (s[i] === '"') { end = i; break; }
        }
        let inner = end >= 0 ? s.slice(open + 1, end) : s.slice(open + 1);
        // 아직 안 닫힌 스트리밍이면, 끝에 매달린 '미완성 백슬래시'(홀수 개)는 떼어
        // JSON.parse 가 깨지지 않게 한다(\\u 처럼 뒤가 잘린 이스케이프도 동일 방어).
        if (end < 0) {
            const m = inner.match(/(\\\\+)$/);
            if (m && m[1].length % 2 === 1) { inner = inner.slice(0, -1); }
            inner = inner.replace(/\\\\u[0-9a-fA-F]{0,3}$/, '');   // 꼬리 잘린 \\uXXXX 방어
        }
        try {
            return JSON.parse('"' + inner + '"');   // 이스케이프 복원은 여기서 자동
        } catch (e) {
            return inner;   // 만에 하나 파싱 실패 시 원문 그대로(표시는 됨)
        }
    }
    // 타임라인 요약을 '제목 / 상세'로 가른다 — 토막 방지가 핵심.
    //  ① AI 가 첫 줄에 제목을 주고 줄바꿈으로 상세를 분리하면 그 줄바꿈만 믿는다(추정 없음).
    //  ② 줄바꿈이 없으면(구버전 캐시 등) 첫 '문장 끝'까지를 제목으로 — 쉼표 중간에서 자르지 않는다.
    function tlSplitSummary(text) {
        const t = String(text || '').replace(/\\r/g, '').trim();
        if (!t) { return { lead: '', rest: '' }; }
        const nl = t.indexOf('\\n');
        if (nl > 0) {
            return { lead: t.slice(0, nl).trim(), rest: t.slice(nl + 1).replace(/^\\n+/, '').trim() };
        }
        const m = t.match(/^[\\s\\S]*?(?:다\\.|요\\.|[.!?])/);
        if (m) { return { lead: m[0].trim(), rest: t.slice(m[0].length).trim() }; }
        return { lead: t, rest: '' };
    }
    // 마일스톤 카드용 분리 — '타이틀 1줄 / 내용 2줄' 카드를 채운다.
    //  ① AI 가 줄바꿈으로 타이틀/내용을 나눠 보내면 그 경계를 그대로 믿는다(tlSplitSummary).
    //  ② 줄바꿈도 문장부호도 없는 한 토막(구버전 캐시 등)이면 → 아래 tlMilestoneFallback 으로 가른다.
    function tlMilestoneSplit(text) {
        const t = String(text || '').replace(/\\r/g, '').trim();
        if (!t) { return { lead: '', rest: '' }; }
        const split = tlSplitSummary(t);
        // tlSplitSummary 가 본문(rest)을 못 만든 단일 토막이면 폴백으로 한 번 더 가른다.
        if (split.rest) { return split; }
        return tlMilestoneFallback(t);
    }
    // TODO(당신이 구현): 줄바꿈·문장부호가 없는 한 문장을 '타이틀 / 내용' 으로 가른다.
    //   - 입력 t 예: "이슈 #54에 대응하는 신규 기능을 개발해 서비스 요구사항을 추가 충족함"
    //   - 반환: { lead: <타이틀 1줄>, rest: <내용> }  (rest 가 비면 타이틀만 표시됨)
    //   판단 포인트(UX 트레이드오프):
    //     · 어디서 자를까? 연결어미('~해/~하고/~며/~로') 기준 첫 토막을 타이틀로? 글자 수(예: 18자)로?
    //     · 자를 곳이 마땅찮으면 통째로 타이틀(rest='')로 둘지, 억지로 반 가를지?
    function tlMilestoneFallback(t) {
        // 여기를 구현하세요. 임시 동작: 분리 없이 통째로 타이틀.
        return { lead: t, rest: '' };
    }
    // 블레임 콜아웃 — 핵심 한 줄(#narrative) / 자세한 배경(#ca-detail).
    function fillCallout(text, headline) {
        fillCalloutEls(
            { lead: 'narrative', detailSec: 'ca-detail-sec', detail: 'ca-detail', more: 'callout-more' },
            text, headline, '변경 사유를 분석할 수 없습니다.',
        );
    }
    // 타임라인 요약 — 제목(#tl-summary) / 상세(#tl-detail). AI 가 넣은 줄바꿈 기준으로 가른다(토막 방지).
    function tlFillSummary(text) {
        fillCalloutSplit(
            { lead: 'tl-summary', detailSec: 'tl-detail-sec', detail: 'tl-detail', more: 'tl-more' },
            tlSplitSummary(text || ''), 'AI 요약을 생성하지 못했습니다.', text || '',
        );
    }
    // 팀/티켓 칩 — 메타 표에 흩어진 핵심을 콜아웃 옆에 모은다. 없는 항목은 건너뛴다.
    function renderCalloutChips(p) {
        const box = document.getElementById('callout-chips');
        if (!box) { return; }
        box.innerHTML = '';
        const add = (icon, text, extra) => {
            if (!text) { return; }
            const c = document.createElement('span');
            c.className = 'ca-chip' + (extra ? ' ' + extra : '');
            c.innerHTML = icon; c.appendChild(document.createTextNode(' ' + text));
            box.appendChild(c);
        };
        add(ICON.users, p.team);
        add(ICON.issue, p.ticket);
        box.classList.toggle('hidden', !box.childNodes.length);
    }
    function blStreaming(p) {
        showTab('blame');
        document.getElementById('empty').classList.add('hidden');
        document.getElementById('info').classList.add('hidden');
        document.getElementById('content').classList.remove('hidden');
        revealTabs(true);

        // 파일 헤더 + 작성자 행(아바타·이름·날짜)을 메타로 먼저 채운다.
        document.getElementById('file-name').textContent = p.fileName;
        document.getElementById('file-line').textContent = 'L' + p.line;
        fillAuthor(p);

        // 콜아웃: 핵심 한 줄 자리(#narrative)에 타이핑될 설명(#ca-exp) + 깜빡이는 캐럿.
        // 핵심/배경 분리·칩은 done(blResult)에서 확정한다.
        blExp = p.text || '';
        const calloutEl = document.getElementById('narrative');
        calloutEl.innerHTML = '<span id="ca-exp"></span><span class="caret"></span>';
        if (blExp) { document.getElementById('ca-exp').innerHTML = renderBold(blExp); }
        document.getElementById('ca-detail-sec').classList.add('hidden');
        document.getElementById('callout-more').classList.add('hidden');
        document.getElementById('callout-chips').classList.add('hidden');

        const histWrap = document.getElementById('history-wrap');
        const histList = document.getElementById('history-list');
        histList.innerHTML = '';
        if (p.lineHistory && p.lineHistory.length) {
            histWrap.classList.remove('hidden');
            p.lineHistory.forEach(h => histList.appendChild(renderHistory(h, p.commitShort)));
        } else {
            histWrap.classList.add('hidden');
        }
    }
    function blDelta(delta) {
        blExp += delta;
        const exp = document.getElementById('ca-exp');
        if (exp) { exp.innerHTML = renderBold(blExp); }
    }
    function blResult(p) {
        // 스트리밍으로 모인 전체 설명을 핵심 한 줄 / 자세한 배경으로 가르고 캐럿을 거둔다.
        blExp = (p.explanation || blExp || '').trim();
        fillCallout(blExp, p.headline);
        renderCalloutChips(p);
    }

    function render(p) {
        document.getElementById('empty').classList.add('hidden');
        document.getElementById('info').classList.add('hidden');
        document.getElementById('content').classList.remove('hidden');
        revealTabs(true);

        // 파일 헤더 + 작성자 행 + 콜아웃(핵심 한 줄 / 자세한 배경) — 스트리밍 없이 곧장 최종 상태.
        document.getElementById('file-name').textContent = p.fileName;
        document.getElementById('file-line').textContent = 'L' + p.line;
        fillAuthor(p);
        fillCallout(p.explanation || '', p.headline);
        renderCalloutChips(p);

        // 라인 수정 이력
        const histWrap = document.getElementById('history-wrap');
        const histList = document.getElementById('history-list');
        histList.innerHTML = '';
        if (p.lineHistory && p.lineHistory.length) {
            histWrap.classList.remove('hidden');
            p.lineHistory.forEach(h => histList.appendChild(renderHistory(h, p.commitShort)));
        } else {
            histWrap.classList.add('hidden');
        }
    }

    // 커밋 해시 → AI 가 다듬은 라인 이력 타이틀(웹뷰 캐시). done 재렌더·재방문 시 스켈레톤
    // 깜빡임 없이 바로 쓰기 위함. 해시는 전역 식별자라 파일/라인이 달라도 재사용 안전.
    let lineTitles = {};
    // 안전망 — 어떤 이유로든 타이틀이 끝내 안 오면(백엔드 다운 등) 스켈레톤이 영구히 남지 않게
    // 원본으로 드러낸다. 정상 경로(성공/실패 모두 setHistoryTitles 호출)는 수 초 내 끝나므로
    // 충분히 길게 둬, 이 타이머가 먼저 터져서 '원본→AI' 교체가 되살아나는 일은 없게 한다.
    let titleFallbackTimer = null;
    function armTitleFallback() {
        if (titleFallbackTimer) { clearTimeout(titleFallbackTimer); }
        titleFallbackTimer = setTimeout(() => {
            titleFallbackTimer = null;
            document.querySelectorAll('#history-list .hist-item__subject.is-loading').forEach(sub => {
                sub.textContent = sub.dataset.raw || '';
                sub.classList.remove('is-loading');
            });
        }, 12000);
    }

    // 라인 수정 이력 한 줄. (행 본문 클릭으로 git show diff 를 열던 기능은 제거됨 —
    // 캐럿=변경사유 펼침, '이슈 N' 배지=이슈 탭 만 동작한다.)
    // currentShort(=블레임 대상 커밋 7자리)와 같은 커밋은 'current' 로 강조한다.
    function renderHistory(h, currentShort) {
        const short = (h.hash || '').slice(0, 7);
        const isCurrent = currentShort && short === currentShort;
        const el = document.createElement('div');
        el.className = 'hist-item' + (isCurrent ? ' current' : '');
        el.dataset.hash = h.hash || '';
        // 배지('이슈 N')만 클릭 동작(openCommitIssues)을 가진다. 캐럿(세모)·변경사유 펼침은 제거됨.
        // 배지를 머리줄(head) 우측에 두어, 타이틀(subject)이 그 아래로 전체 폭을 쓰게 한다.
        const badge = (h.issueCount && h.issueCount > 0)
            ? '<span class="hist-item__issues" data-action="openCommitIssues" title="이 커밋이 참조한 이슈 보기"><span class="ico">' + ICON.issue + '</span>이슈 ' + h.issueCount + '</span>'
            : '';
        el.innerHTML =
            '<span class="hist-item__dot"></span>' +
            '<div style="min-width:0">' +
                '<div class="hist-item__head">' +
                    '<span class="hist-item__hash mono"></span>' +
                    '<span class="hist-item__date"></span>' +
                    badge +
                '</div>' +
                '<div class="hist-item__subject"></div>' +
                '<div class="hist-item__author"></div>' +
            '</div>';
        el.querySelector('.hist-item__hash').textContent = short;
        el.querySelector('.hist-item__date').textContent = formatHistDate(h.date);
        // 타이틀: 원본 메시지를 보여줬다가 갈아끼우면 어색하므로, AI 타이틀이 이미 캐시돼 있으면
        // 바로 쓰고, 아니면 스켈레톤만 띄운다(applyHistoryTitles 가 도착 시 드러냄). 원본은 폴백용으로 보관.
        const subEl = el.querySelector('.hist-item__subject');
        subEl.dataset.raw = h.subject || '';
        const cached = lineTitles[h.hash || ''];
        if (cached) { subEl.textContent = cached; }
        else { subEl.classList.add('is-loading'); armTitleFallback(); }
        el.querySelector('.hist-item__author').textContent = h.author || '';
        // '이슈 N' 배지도 자기 커밋 해시를 실어, 클릭 시 그 커밋의 이슈만 역추적하게 한다.
        const issuesBadge = el.querySelector('.hist-item__issues');
        if (issuesBadge) { issuesBadge.dataset.hash = h.hash || ''; }
        return el;
    }


    // 펼친 라인 이력 항목에 그 커밋의 변경 사유를 채운다(지연 로드 응답).
    function fillHistoryReason(hash, reason) {
        const box = document.querySelector('.hist-item__reason[data-hash="' + hash + '"]');
        if (!box) { return; }
        box.classList.remove('loading');
        box.innerHTML = decorate(reason || '(변경 사유 없음)');
        box.dataset.loaded = '1';
        delete box.dataset.loading;
    }

    // 배치 응답 도착 — '이번에 받은 해시'의 스켈레톤만 드러낸다(텍스트 교체가 아니라 '등장').
    // 받지 않은 행은 스켈레톤을 유지한다 — 아직 fetch 중인 새 커밋을 원본으로 먼저 드러냈다가
    // AI 로 다시 바꾸는(어색한 교체) 일을 막기 위함. 백엔드는 요청 해시마다 항상 한 줄을
    // 돌려주므로(다듬은 것 or 원본), 한 번의 응답으로 그 분석의 모든 행이 드러난다.
    function applyHistoryTitles(titles) {
        titles = titles || {};
        Object.assign(lineTitles, titles);   // 해시별 캐시 갱신(재렌더·재방문 대비)
        document.querySelectorAll('#history-list .hist-item').forEach(row => {
            const hash = row.dataset.hash || '';
            if (!(hash in titles)) { return; }   // 이번에 받은 해시만 드러낸다
            const sub = row.querySelector('.hist-item__subject');
            if (!sub) { return; }
            const wasLoading = sub.classList.contains('is-loading');
            sub.textContent = titles[hash] || sub.dataset.raw || '';
            sub.classList.remove('is-loading');
            if (wasLoading && titles[hash]) { sub.classList.add('hist-item__subject--ai'); }
        });
        // 남은 스켈레톤이 없으면 안전망 타이머는 불필요.
        if (titleFallbackTimer && !document.querySelector('#history-list .hist-item__subject.is-loading')) {
            clearTimeout(titleFallbackTimer); titleFallbackTimer = null;
        }
    }

    // "2026-03-15" → "3월 15일" (파싱 실패 시 원본)
    function formatHistDate(s) {
        const m = String(s || '').match(/^(\\d{4})-(\\d{1,2})-(\\d{1,2})/);
        return m ? (Number(m[2]) + '월 ' + Number(m[3]) + '일') : (s || '');
    }

    function decorate(s) {
        // "..." 인용은 코드 강조로, 줄바꿈은 <br> 로 변환 (메타/본문 분리 렌더링용)
        const esc = String(s)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
        return esc
            .replace(/"([^"]+)"/g, '<code>$1</code>')
            // ⚠️ 이 함수는 TS 템플릿 리터럴 안의 웹뷰 스크립트다. 개행 메타문자는 반드시
            // 백슬래시를 이중(아래처럼)으로 써야 한다. 단일로 쓰면 컴파일 시 실제 줄바꿈으로
            // 치환돼 생성된 HTML 의 정규식이 줄을 넘겨 깨진다(Invalid regular expression).
            // 주석에도 단일 개행 메타문자를 적지 말 것 — 주석 자체가 두 줄로 쪼개져 깨진다.
            .replace(/\\n/g, '<br/>');
    }

    function fileKind(name) {
        const ext = (name.split('.').pop() || '?').toUpperCase();
        return ext.charAt(0);
    }

    function toggle(id, on) {
        document.getElementById(id).classList.toggle('hidden', !on);
    }

    // ── 공통 탭바: 클릭 시 해당 페인으로 전환 + 확장에 분석 요청 ─────
    const PANES = { blame: 'pane-blame', timeline: 'pane-timeline', issue: 'pane-issue' };
    function showTab(tab) {
        if (!PANES[tab]) { tab = 'blame'; }
        // 타임라인·이슈로 가면 탭 바를 드러낸다(블레임은 환영 화면일 수 있어 건드리지 않음).
        if (tab !== 'blame') { revealTabs(true); }
        document.querySelectorAll('.tab').forEach(t => t.classList.toggle('active', t.dataset.tab === tab));
        Object.keys(PANES).forEach(k => document.getElementById(PANES[k]).classList.toggle('hidden', k !== tab));
    }
    document.querySelector('.tabs').addEventListener('click', (e) => {
        const tab = e.target.closest('.tab');
        if (!tab) return;
        showTab(tab.dataset.tab);
        // 확장에 알려 현재 커서 라인 기준으로 해당 분석을 자동 실행시킨다.
        vscode.postMessage({ type: 'switchTab', payload: { tab: tab.dataset.tab } });
    });

    // ── 이슈 목록: 상태 필터 탭 + 검색(둘 다 클라이언트 필터, 재요청 없음) ──
    document.getElementById('is-filters').addEventListener('click', (e) => {
        const btn = e.target.closest('.is-filter');
        if (!btn) { return; }
        isFilter = btn.dataset.filter || 'all';
        renderIssueList();
    });
    document.getElementById('is-search-input').addEventListener('input', (e) => {
        isQuery = e.target.value || '';
        renderIssueList();
    });

    // ── 버튼·링크 액션 위임 ───────────────────────────────────────
    document.body.addEventListener('click', (e) => {
        const el = e.target.closest('[data-action]');
        if (!el) return;
        // 환영 화면 CTA — 현재 커서 라인 기준으로 블레임 분석을 시작한다.
        if (el.dataset.action === 'analyzeFile') {
            revealTabs(true);
            showTab('blame');
            vscode.postMessage({ type: 'switchTab', payload: { tab: 'blame' } });
            return;
        }
        // 타임라인 파일명 옆 휴지통 — 이 파일의 타임라인 캐시를 비운다(확장이 명령 실행).
        if (el.dataset.action === 'clearTimelineCache') {
            vscode.postMessage({ type: 'clearTimelineCache' });
            return;
        }
        // 돋보기 파일명 옆 휴지통 — 이 파일의 돋보기 설명 캐시를 비운다(시연 재분석용).
        if (el.dataset.action === 'clearBlameCache') {
            vscode.postMessage({ type: 'clearBlameCache' });
            return;
        }
        // 콜아웃 '더 보기/접기' — 같은 카드 안 '자세한 배경'을 통째로 접고/펼친다.
        // 블레임·타임라인이 같은 버튼을 쓰므로, 클릭한 버튼이 속한 .callout 기준으로 찾는다.
        if (el.dataset.action === 'toggleCallout') {
            const card = el.closest('.callout');
            const sec = card ? card.querySelector('.callout__detail') : null;
            if (!sec) { return; }
            const collapsed = sec.classList.toggle('hidden');
            el.classList.toggle('expanded', !collapsed);
            el.textContent = collapsed ? '더 보기' : '접기';
            return;
        }
        // 라인 수정 이력 캐럿/이유 박스 클릭 — 행을 펼치고(토글) 그 커밋 사유를 지연 로드한다.
        if (el.dataset.action === 'expandHistory') {
            const item = el.closest('.hist-item');
            if (!item) { return; }
            const box = item.querySelector('.hist-item__reason');
            const expanded = item.classList.toggle('expanded');
            if (box) { box.classList.toggle('hidden', !expanded); }
            // 처음 펼칠 때만 요청 — loaded/loading 가드로 중복 호출을 막는다.
            if (expanded && box && !box.dataset.loaded && !box.dataset.loading) {
                box.dataset.loading = '1';
                box.classList.add('loading');
                box.textContent = '변경 사유 불러오는 중…';
                vscode.postMessage({ type: 'expandHistory', payload: { hash: el.dataset.hash } });
            }
            return;
        }
        // 이슈 목록 항목 선택 — 외부로 열지 않고 상세 화면으로 전환한다.
        if (el.dataset.action === 'openIssueDetail') {
            openIssueDetail(parseInt(el.dataset.index, 10) || 0);
            return;
        }
        // '라인 수정 이력'의 '이슈 N' 배지 — 이슈 탭으로 전환해 그 커밋의 이슈만 보여준다.
        if (el.dataset.action === 'openCommitIssues') {
            vscode.postMessage({ type: 'openCommitIssues', payload: { hash: el.dataset.hash } });
            return;
        }
        // 커밋 스코프 배너의 '파일 전체 이슈로' — 파일 단위 이슈 검색을 다시 띄운다.
        if (el.dataset.action === 'issueBackToFile') {
            vscode.postMessage({ type: 'switchTab', payload: { tab: 'issue' } });
            return;
        }
        // 상세 화면 네비게이션
        if (el.dataset.action === 'issueBack') { showIssueList(); return; }
        if (el.dataset.action === 'issuePrev') { stepIssue(-1); return; }
        if (el.dataset.action === 'issueNext') { stepIssue(1); return; }
        // AI 질문 — 현재 이슈를 컨텍스트로 한 챗봇 화면으로 전환.
        if (el.dataset.action === 'issueAiAsk') {
            openIssueChat();
            return;
        }
        // 챗봇 화면 — 상세로 복귀 / 전송.
        if (el.dataset.action === 'issueChatBack') { showIssueDetail(); return; }
        if (el.dataset.action === 'issueChatSend') { sendChat(); return; }
        // 이슈/첨부 항목은 자기 URL 을 함께 실어 외부로 연다.
        if (el.dataset.action === 'openIssue') {
            vscode.postMessage({ type: 'openIssue', payload: { url: el.dataset.url } });
            return;
        }
        // 이미지(본문/첨부) 클릭 — 확대 팝업으로.
        if (el.dataset.action === 'zoomImage') {
            showLightbox(el.dataset.url);
            return;
        }
        vscode.postMessage({ type: el.dataset.action });
    });

    // ── 이미지 확대 팝업(라이트박스) ──────────────────────────────────────
    // 첫 호출 때 오버레이를 만들어 재사용한다. 배경/이미지 클릭 또는 ESC 로 닫는다.
    let lightboxEl = null;
    function showLightbox(url) {
        const clean = cleanUrl(url);
        if (!clean) { return; }
        if (!lightboxEl) {
            lightboxEl = document.createElement('div');
            lightboxEl.className = 'is-lightbox hidden';
            lightboxEl.innerHTML = '<img alt="확대 이미지"/><div class="is-lightbox__hint">클릭 또는 ESC로 닫기</div>';
            lightboxEl.addEventListener('click', hideLightbox);
            document.body.appendChild(lightboxEl);
        }
        lightboxEl.querySelector('img').src = clean;
        lightboxEl.classList.remove('hidden');
    }
    function hideLightbox() {
        if (lightboxEl) { lightboxEl.classList.add('hidden'); lightboxEl.querySelector('img').src = ''; }
    }
    window.addEventListener('keydown', (e) => {
        if (e.key === 'Escape' && lightboxEl && !lightboxEl.classList.contains('hidden')) { hideLightbox(); }
    });

    // ── 마우스 '뒤로' 사이드 버튼 → 이슈 상세에서 목록으로 ─────────────────
    // 다수 마우스의 뒤로(back) 버튼은 button 3 으로 들어온다(앞으로는 4). 이슈 상세가
    // 떠 있을 때만 가로채 목록으로 되돌리고, 그 외 화면에선 기본 동작에 맡긴다.
    window.addEventListener('mouseup', (e) => {
        if (e.button !== 3) { return; }
        const detail = document.getElementById('is-detail-view');
        if (detail && !detail.classList.contains('hidden')) {
            e.preventDefault();
            showIssueList();
        }
    });

    // ── 핸드셰이크: 리스너가 모두 등록된 지금 시점에 extension 으로 'ready' 통지 ──
    // 이 신호 이전에 온 render 메시지는 유실되므로, extension 은 ready 를 받고 나서
    // 보류해 둔 마지막 분석 결과를 보내준다.
    vscode.postMessage({ type: 'ready' });
</script>
</body>
</html>`;
    }
}

// ─── 유틸 ─────────────────────────────────────────────────────────────────────
function randomNonce(): string {
    const chars = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789';
    let s = '';
    for (let i = 0; i < 32; i++) { s += chars.charAt(Math.floor(Math.random() * chars.length)); }
    return s;
}

/**
 * 한글 조사 선택 — 단어의 마지막 글자에 받침(종성)이 있으면 withFinal, 없으면 withoutFinal.
 *
 * 한글 음절은 0xAC00 부터 28칸 간격으로 종성이 순환한다.
 * (code - 0xAC00) % 28 === 0 이면 종성 없음(받침 없음).
 * 한글이 아닌 문자(영문 이름·숫자 등)로 끝나면 받침 없음으로 처리한다.
 *
 * 예: josa('홍길동', '이', '가') → '이' / josa('철수', '이', '가') → '가'
 */
function josa(word: string, withFinal: string, withoutFinal: string): string {
    const ch = (word ?? '').trim().slice(-1);
    const code = ch.charCodeAt(0);
    const isHangul = code >= 0xac00 && code <= 0xd7a3;
    if (!isHangul) { return withoutFinal; }
    return (code - 0xac00) % 28 !== 0 ? withFinal : withoutFinal;
}

function formatDisplayDate(s: string): string {
    const d = parseDateLoose(s);
    if (!d) { return s; }
    return `${d.getMonth() + 1}월 ${d.getDate()}일`;
}

/** 메타 표의 '날짜' 칸용 — "2026-03-15" ISO 형식. 파싱 실패 시 원본 반환. */
function formatISODate(s: string): string {
    const d = parseDateLoose(s);
    if (!d) { return s; }
    const mm = String(d.getMonth() + 1).padStart(2, '0');
    const dd = String(d.getDate()).padStart(2, '0');
    return `${d.getFullYear()}-${mm}-${dd}`;
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
