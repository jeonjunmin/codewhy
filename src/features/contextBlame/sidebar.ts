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
    | { kind: 'streaming'; fileName: string; text: string }
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
    | { kind: 'empty'; message?: string };

export class ContextBlameSidebarProvider implements vscode.WebviewViewProvider {
    private view?: vscode.WebviewView;
    private last?: { ctx: EditorContext; result: BlameResult; pinned: boolean };
    private lastBlameStream?: BlameStreamState;
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
            // 이슈 기능 개발 전까지 '이슈 N' 배지 클릭의 임시 동작(안내). 실제 이동은 TODO.
            onOpenIssueTodo: () => void;
            // 라인 수정 이력 항목 펼침 — 그 커밋의 변경 사유를 지연 생성한다.
            onExpandHistory: (hash: string, filePath: string, repoPath: string) => void;
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
        } else {
            const r = s.result;
            wv.postMessage({
                type: 'blResult',
                payload: {
                    explanation: (r.explanation ?? '').trim(),
                    sourceRef: r.sourceRef ?? r.specRef ?? null,
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

    private postIssue(s: IssueState) {
        const wv = this.view?.webview;
        if (!wv || !this.ready) { return; }
        if (s.kind === 'loading') {
            wv.postMessage({ type: 'isLoading' });
        } else if (s.kind === 'result') {
            wv.postMessage({ type: 'isResult', payload: { line: s.line, fileName: s.fileName, documents: s.result.documents } });
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
        // '라인 수정 이력' 이슈 배지 클릭 — 이슈 기능 미완이라 실제 URL 이 없어 임시 안내만 한다.
        if (msg.type === 'openIssueTodo') {
            this.handlers.onOpenIssueTodo();
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
            case 'openCommitHash':
                if (typeof msg.payload?.hash === 'string' && msg.payload.hash) {
                    this.handlers.onOpenCommit(msg.payload.hash, ctx.repoPath);
                }
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
    }

    // ─── 한 번만 그리는 HTML 빨대 컵 ──────────────────────────────────────
    private renderHtml(): string {
        const nonce = randomNonce();
        const csp = [
            `default-src 'none'`,
            `style-src ${this.view?.webview.cspSource ?? "'self'"} 'unsafe-inline'`,
            `script-src 'nonce-${nonce}'`,
            `img-src ${this.view?.webview.cspSource ?? "'self'"} data:`,
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
        border-radius: 12px;
        padding: 12px 14px;
    }
    .callout__title {
        display: inline-flex; align-items: center; gap: 6px;
        color: var(--accent-violet);
        font-size: 11.5px; font-weight: 600;
        margin-bottom: 6px;
    }
    .callout__body {
        color: var(--fg); font-size: 13px; line-height: 1.6;
    }
    .callout__body .ca-author { color: var(--accent-violet); font-weight: 700; }
    .callout__body code {
        background: var(--code-bg); color: var(--code-fg);
        padding: 1px 5px; border-radius: 4px; font-size: 11.5px;
    }

    /* ── 파일 브레드크럼 ─────────────────────────────────────────── */
    .crumb {
        display: flex; align-items: center; gap: 6px;
        padding: 6px 0; border-bottom: 1px solid var(--line);
        color: var(--fg-dim); font-size: 12px;
    }
    .crumb__icon {
        width: 16px; height: 16px;
        display: inline-flex; align-items: center; justify-content: center;
        background: #6D28D9; color: #fff; border-radius: 3px;
        font-size: 10px; font-weight: 700;
    }
    .crumb__sep { color: var(--fg-mute); }
    .crumb__line { color: var(--fg-dim); }
    .crumb__dot {
        margin-left: auto; width: 7px; height: 7px;
        background: var(--accent-cyan); border-radius: 50%;
        box-shadow: 0 0 8px rgba(103,232,249,0.55);
    }

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
        grid-template-columns: 16px 1fr auto;
        column-gap: 8px;
        padding: 0 0 14px 0;
        cursor: pointer;
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
        color: var(--fg); font-size: 12.5px; margin-top: 3px;
        overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
    }
    .hist-item__author { color: var(--fg-mute); font-size: 11px; margin-top: 2px; }
    .hist-item__issues {
        align-self: start; margin-top: 1px;
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

    /* ── 연관 이슈 롤업 (라인 전체 dedup + 상태) ─────────────────── */
    .lineissues { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 16px; }
    .li-chip {
        display: inline-flex; align-items: center; gap: 5px;
        padding: 3px 9px; border-radius: 7px; font-size: 11px; white-space: nowrap;
        background: var(--surface); border: 1px solid var(--line); color: var(--fg-dim);
        cursor: pointer;
    }
    .li-chip .ico { display: inline-flex; opacity: 0.8; }
    .li-chip__num { font-weight: 600; color: var(--fg); }
    .li-chip__status { color: var(--fg-mute); font-size: 10px; }
    .li-chip:hover { border-color: var(--accent-violet); color: var(--fg); }
    /* 현재 = 지금의 변경 동인 */
    .li-chip--current { border-color: var(--line-soft); }
    .li-chip--current .li-chip__num,
    .li-chip--current .li-chip__status { color: var(--accent-violet); }
    /* 되돌림 = revert 커밋이 참조(휴리스틱) */
    .li-chip--reverted { border-color: rgba(248,113,113,0.40); }
    .li-chip--reverted .li-chip__status { color: #F87171; }
    .li-chip--reverted .li-chip__num { text-decoration: line-through; opacity: 0.85; }

    /* ── 작은 보조 ───────────────────────────────────────────────── */
    .hidden { display: none !important; }

    /* ── 탭 페인 ─────────────────────────────────────────────────── */
    .pane.hidden { display: none !important; }

    /* ── 타임라인 페인 ───────────────────────────────────────────── */
    .file-chip {
        display: inline-flex; align-items: center; gap: 5px;
        font-size: 11px; color: var(--fg-dim);
        background: var(--surface); border: 1px solid var(--line);
        border-radius: 6px; padding: 3px 9px;
        max-width: 100%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
    }
    .file-chip-name { font-weight: 600; color: var(--fg); }
    .ai-card {
        background: var(--callout-bg);
        border: 1px solid var(--line-soft);
        border-radius: 12px; padding: 12px 14px;
    }
    .ai-card__label {
        display: inline-flex; align-items: center; gap: 6px;
        color: var(--accent-violet); font-size: 11.5px; font-weight: 600;
        margin-bottom: 6px;
    }
    .ai-card__text { color: var(--fg); font-size: 12.5px; line-height: 1.7; }
    .ai-card__text strong { color: #fff; font-weight: 700; }
    .caret {
        display: inline-block; width: 2px; height: 1em; margin-left: 2px;
        vertical-align: text-bottom; background: var(--accent-violet);
        animation: caret-blink 0.9s steps(1) infinite;
    }
    @keyframes caret-blink { 0%,49% { opacity: 1; } 50%,100% { opacity: 0; } }
    .tl-list { display: flex; flex-direction: column; }
    .tl-item { display: flex; gap: 11px; }
    .tl-item__left { display: flex; flex-direction: column; align-items: center; flex-shrink: 0; width: 24px; }
    .tl-item__badge {
        width: 22px; height: 22px; border-radius: 50%;
        display: inline-flex; align-items: center; justify-content: center;
        font-size: 10.5px; font-weight: 700; color: #fff; flex-shrink: 0;
    }
    .tl-item__rail { width: 2px; flex: 1; min-height: 10px; background: var(--line); margin: 4px 0; }
    .tl-item:last-child .tl-item__rail { display: none; }
    .tl-item__right { flex: 1; min-width: 0; padding-bottom: 16px; }
    .tl-item:last-child .tl-item__right { padding-bottom: 0; }
    .tl-item__date { font-size: 11px; color: var(--fg-mute); margin: 2px 0 4px; }
    .tl-item__date .mon { color: var(--fg-dim); font-weight: 700; }
    .tl-item__title { font-size: 12.5px; font-weight: 600; color: var(--fg); line-height: 1.4; }
    .tl-item__desc { font-size: 11.5px; color: var(--fg-dim); margin-top: 3px; line-height: 1.6; }

    /* ── 이슈 페인 (요구사항 역추적) ─────────────────────────────── */
    #is-list-view { display: flex; flex-direction: column; gap: 11px; }

    /* 목록 헤더: '요구사항 추적' + 파일 칩 */
    .is-l-head { display: flex; flex-direction: column; gap: 8px; }
    .is-l-head__title { color: var(--fg); font-size: 14px; font-weight: 700; letter-spacing: -0.01em; }
    .is-l-file {
        display: inline-flex; align-items: center; gap: 6px; align-self: flex-start;
        color: var(--fg-dim); font-size: 12px;
    }
    .is-l-file__kind {
        width: 16px; height: 16px; flex-shrink: 0;
        display: inline-flex; align-items: center; justify-content: center;
        background: #6D28D9; color: #fff; border-radius: 3px; font-size: 10px; font-weight: 700;
    }
    .is-l-file .mono { color: var(--fg); }

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

    /* 상태 필터 탭 (전체/열림/닫힘/초안) */
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
    .is-item__state.draft { color: var(--accent-violet); }
    .is-item__state.draft::before { background: var(--accent-violet); }
    .is-item__num { color: var(--fg-mute); font-size: 11px; font-weight: 600; }
    .is-item__badge {
        flex-shrink: 0; font-size: 10.5px; padding: 2px 7px; border-radius: 6px;
        border: 1px solid var(--line);
    }
    .is-item__head .is-item__badge { margin-left: auto; }
    .is-item__badge.ok { color: var(--accent-cyan); border-color: rgba(103,232,249,0.4); }
    .is-item__badge.guess { color: var(--fg-mute); }
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
        color: var(--fg-mute); font-size: 10.5px;
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
    .is-d-back {
        display: inline-flex; align-items: center; gap: 3px;
        background: none; border: none; padding: 0; cursor: pointer;
        color: var(--fg-dim); font-size: 12px; font-family: inherit;
    }
    .is-d-back:hover { color: var(--fg); }
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
    .is-d-state.draft { color: var(--accent-violet); border-color: var(--line-soft); }
    .is-d-state.draft::before { background: var(--accent-violet); }
    .is-d-num { color: var(--fg-mute); font-size: 12px; font-weight: 600; }
    .is-d-ai {
        flex-shrink: 0; display: inline-flex; align-items: center; gap: 5px;
        padding: 4px 12px; border-radius: 8px; cursor: pointer; border: none;
        color: #fff; font-size: 11.5px; font-weight: 600; font-family: inherit;
        background: var(--grad);
    }
    .is-d-ai:hover { filter: brightness(1.12); }

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
    .is-d-meta .avatar {
        display: inline-flex; align-items: center; justify-content: center;
        width: 16px; height: 16px; border-radius: 50%; background: var(--accent-violet);
        color: #18181B; font-size: 9px; font-weight: 700; margin-right: 5px; vertical-align: -3px;
    }

    .is-d-body { color: var(--fg-dim); font-size: 12.5px; line-height: 1.7; word-break: break-word; }
    .is-d-body code { background: var(--code-bg); color: var(--code-fg); padding: 1px 5px; border-radius: 4px; font-size: 11.5px; }

    .is-d-sec-title { color: var(--fg-mute); font-size: 11px; font-weight: 600; display: flex; align-items: center; gap: 6px; }
    .is-d-atts { display: flex; flex-direction: column; gap: 6px; margin-top: -4px; }
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

    /* ── 로딩 스피너 ─────────────────────────────────────────────── */
    .spinner {
        display: inline-block; width: 13px; height: 13px; vertical-align: -2px;
        border: 2px solid var(--line); border-top-color: var(--accent-violet);
        border-radius: 50%; animation: spin .75s linear infinite; margin-right: 6px;
    }
    @keyframes spin { to { transform: rotate(360deg); } }
</style>
</head>
<body>
    <nav class="tabs hidden">
        <button class="tab active" data-tab="blame"><span class="tab__ico" id="ico-tab-blame"></span>블레임</button>
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
        <section class="callout">
            <div class="callout__title">
                <span id="ico-callout"></span> 이 라인이 추가된 이유
            </div>
            <div class="callout__body" id="narrative"></div>
        </section>

        <div class="crumb">
            <span class="crumb__icon" id="file-kind">K</span>
            <span class="mono" id="file-name"></span>
            <span class="crumb__sep">›</span>
            <span class="crumb__line mono" id="file-line"></span>
            <span class="crumb__dot"></span>
        </div>

        <dl class="meta">
            <dt>작성자</dt><dd class="author"><strong id="author-name"></strong><span id="author-team-wrap" class="hidden">&nbsp;·&nbsp;<span id="author-team"></span></span></dd>
            <dt>커밋</dt><dd><a class="link mono" id="meta-commit" data-action="openCommit"></a><span id="meta-ticket-wrap" class="hidden"> — <span class="ticket" id="meta-ticket"></span></span></dd>
            <dt>날짜</dt><dd><span id="meta-date"></span><span id="meta-relative-wrap" class="hidden"> <span style="color:var(--fg-mute)">(<span id="meta-relative"></span>)</span></span></dd>
            <dt>변경</dt><dd id="meta-change">—</dd>
            <dt>출처</dt><dd id="meta-source">—</dd>
        </dl>

        <section id="lineissues-wrap" class="hidden">
            <div class="history__title">연관 이슈</div>
            <div class="lineissues" id="lineissues-list"></div>
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
            <div class="file-chip"><span>📄</span><span class="file-chip-name" id="tl-file"></span></div>
            <div class="ai-card">
                <div class="ai-card__label"><span id="ico-tl-spark"></span> AI 요약</div>
                <div class="ai-card__text" id="tl-summary"></div>
            </div>
            <section id="tl-ms-wrap" class="hidden">
                <div class="related__title">주요 마일스톤 <span id="tl-ms-count"></span></div>
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
                <div class="is-l-head">
                    <span class="is-l-head__title">요구사항 추적</span>
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
                    <button class="is-filter" data-filter="draft">초안 <span class="is-filter__n" data-count="draft">0</span></button>
                </div>
                <div class="related__list" id="is-list"></div>
                <div id="is-list-empty" class="empty hidden">검색 결과가 없습니다.</div>
            </div>
            <div id="is-detail-view" class="is-detail hidden"></div>
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
    };
    // 아이콘 주입은 보조 장식이므로, 한 요소가 없더라도 핸드셰이크(ready)까지 죽지 않게 격리한다.
    try {
        const setIcon = (id, svg) => { const el = document.getElementById(id); if (el) { el.innerHTML = svg; } };
        setIcon('ico-callout', ICON.spark);
        setIcon('ico-info', ICON.spark);
        setIcon('ico-tab-blame', ICON.doc);
        setIcon('ico-tab-timeline', ICON.clock);
        setIcon('ico-tab-issue', ICON.branch);
        setIcon('ico-tl-spark', ICON.spark);
        setIcon('ico-hero-shield', ICON.shieldBig);
        setIcon('ico-hero-spark', ICON.sparkBig);
        setIcon('ico-hero-check', ICON.check);
        setIcon('ico-is-search', ICON.search);
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
            case 'tlResult': tlResult(msg.payload); break;
            case 'tlEmpty': tlEmpty(msg.payload && msg.payload.message); break;
            // ── 이슈 ──
            case 'isLoading': isLoading(); break;
            case 'isResult': isResult(msg.payload); break;
            case 'isEmpty': isEmpty(msg.payload && msg.payload.message); break;
        }
    });

    // ─────────────────── 타임라인 렌더 ───────────────────
    let tlText = '';
    function tlShow(which) {
        document.getElementById('tl-empty').classList.toggle('hidden', which !== 'empty');
        document.getElementById('tl-body').classList.toggle('hidden', which === 'empty');
    }
    function tlStreaming(p) {
        tlText = p.text || '';
        document.getElementById('tl-file').textContent = p.fileName || '';
        document.getElementById('tl-summary').innerHTML = tlText
            ? renderBold(tlText) + '<span class="caret"></span>'
            : '<span class="spinner"></span>AI가 소스 코드를 분석 중입니다…';
        document.getElementById('tl-ms-wrap').classList.add('hidden');
        tlShow('body');
    }
    function tlDelta(delta) {
        tlText += delta;
        document.getElementById('tl-summary').innerHTML = renderBold(tlText) + '<span class="caret"></span>';
    }
    function tlResult(p) {
        document.getElementById('tl-file').textContent = p.fileName || '';
        document.getElementById('tl-summary').innerHTML = renderBold(p.summary || '');
        const wrap = document.getElementById('tl-ms-wrap');
        const list = document.getElementById('tl-list');
        list.innerHTML = '';
        const ms = (p.milestones || []).slice().sort((a, b) => String(a.date).localeCompare(String(b.date)));
        if (ms.length) {
            wrap.classList.remove('hidden');
            document.getElementById('tl-ms-count').textContent = ms.length + '건';
            ms.forEach((m, i) => list.appendChild(renderMilestone(m, i)));
        } else {
            wrap.classList.add('hidden');
        }
        tlShow('body');
    }
    function tlEmpty(message) {
        if (message) { document.getElementById('tl-empty').innerHTML = decorate(message); }
        tlShow('empty');
    }
    const TL_COLORS = ['#e05454','#2cb8b8','#8b5cf6','#d97706','#16a34a','#3b82f6','#ec4899','#f97316'];
    const KO_MONTHS = ['1월','2월','3월','4월','5월','6월','7월','8월','9월','10월','11월','12월'];
    function renderMilestone(m, i) {
        const el = document.createElement('div');
        el.className = 'tl-item';
        const color = TL_COLORS[i % TL_COLORS.length];
        const mon = KO_MONTHS[(parseInt(String(m.date).split('-')[1], 10) || 0) - 1] || '';
        const s = String(m.description || '').trim();
        const cut = s.search(/[.。,，\\n]/);
        const hasSplit = cut > 0 && cut < 30;
        const title = hasSplit ? s.slice(0, cut).trim() : s;
        const body = hasSplit ? s.slice(cut + 1).trim() : '';
        el.innerHTML =
            '<div class="tl-item__left"><div class="tl-item__badge" style="background:' + color + '">' + (i + 1) + '</div><div class="tl-item__rail"></div></div>' +
            '<div class="tl-item__right">' +
                '<div class="tl-item__date">' + (mon ? '<span class="mon">' + mon + '</span> · ' : '') + '<span></span></div>' +
                '<div class="tl-item__title"></div>' +
                (body ? '<div class="tl-item__desc"></div>' : '') +
            '</div>';
        el.querySelector('.tl-item__date span:last-child').textContent = m.date || '';
        el.querySelector('.tl-item__title').textContent = title;
        if (body) { el.querySelector('.tl-item__desc').textContent = body; }
        return el;
    }
    function renderBold(t) {
        return decorate(t).replace(/\\*\\*(.+?)\\*\\*/g, '<strong>$1</strong>');
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
    const IS_BADGE = {
        issue:    { label: '✓ Issue 연결', cls: 'ok' },
        ticket:   { label: '✓ 티켓 정확', cls: 'ok' },
        semantic: { label: '≈ 추정(검색)', cls: 'guess' },
    };
    // 이슈 탭 상태 — 목록과 상세를 한 데이터(isDocs)로 공유하고 isIndex 로 상세 대상을 가린다.
    // isQuery(검색어)·isFilter(상태 탭)는 목록 뷰 상태로, 데이터 재요청 없이 클라이언트에서만 거른다.
    let isDocs = [];
    let isLine = 0;
    let isFileName = '';
    let isIndex = 0;
    let isQuery = '';
    let isFilter = 'all';   // all | open | closed | draft

    // 이슈 상태를 필터 버킷으로 분류한다. 백엔드 state(open/closed)에 더해
    // 'draft'(초안)도 받을 수 있게 열어 둔다(미전송 시 초안 탭은 0건).
    function issueBucket(d) {
        const s = String((d && d.state) || '').toLowerCase();
        if (s === 'closed') { return 'closed'; }
        if (s === 'draft') { return 'draft'; }
        return 'open';
    }

    function isResult(p) {
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
    function showIssueList() {
        document.getElementById('is-list-view').classList.remove('hidden');
        document.getElementById('is-detail-view').classList.add('hidden');
    }
    function showIssueDetail() {
        document.getElementById('is-list-view').classList.add('hidden');
        document.getElementById('is-detail-view').classList.remove('hidden');
    }
    function renderIssueList() {
        const list = document.getElementById('is-list');
        list.innerHTML = '';

        // 상태별 건수 — 필터 탭 배지 갱신.
        const counts = { all: isDocs.length, open: 0, closed: 0, draft: 0 };
        isDocs.forEach(d => { counts[issueBucket(d)]++; });
        document.querySelectorAll('#is-filters .is-filter__n').forEach(n => {
            n.textContent = counts[n.dataset.count] != null ? counts[n.dataset.count] : 0;
        });
        document.querySelectorAll('#is-filters .is-filter').forEach(b => {
            b.classList.toggle('active', b.dataset.filter === isFilter);
        });

        // 검색어(제목/번호/라벨) + 상태 필터로 거른다. 원본 인덱스를 유지해 상세 이동이 어긋나지 않게 한다.
        const q = isQuery.trim().toLowerCase();
        let shown = 0;
        isDocs.forEach((d, i) => {
            if (isFilter !== 'all' && issueBucket(d) !== isFilter) { return; }
            if (q) {
                const hay = [d.title || '', d.issueNumber != null ? ('#' + d.issueNumber) : '', (d.labels || []).join(' ')]
                    .join(' ').toLowerCase();
                if (hay.indexOf(q) === -1) { return; }
            }
            list.appendChild(renderIssueItem(d, i));
            shown++;
        });
        document.getElementById('is-list-empty').classList.toggle('hidden', shown > 0);
    }
    // 상태 버킷 → 표시 라벨/클래스 (열림/닫힘/초안).
    const IS_STATE = {
        open:   { label: '열림', cls: 'open' },
        closed: { label: '닫힘', cls: 'closed' },
        draft:  { label: '초안', cls: 'draft' },
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
            '<span class="is-item__num"></span>';
        head.querySelector('.is-item__state').textContent = st.label;
        head.querySelector('.is-item__num').textContent = (d.issueNumber != null) ? ('#' + d.issueNumber) : '';
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
            const c = document.createElement('span'); c.textContent = '💬 ' + d.commentCount; right.appendChild(c);
        }
        if (attCount) {
            const a = document.createElement('span'); a.textContent = '📎 ' + attCount; right.appendChild(a);
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
    function openIssueDetail(i) {
        if (!isDocs.length) { return; }
        isIndex = Math.max(0, Math.min(i, isDocs.length - 1));
        renderIssueDetail();
        showIssueDetail();
    }
    function appendMetaRow(dl, term, value, strong) {
        const dt = document.createElement('dt'); dt.textContent = term;
        const dd = document.createElement('dd');
        if (strong) { const st = document.createElement('strong'); st.textContent = value; dd.appendChild(st); }
        else { dd.textContent = value; }
        dl.appendChild(dt); dl.appendChild(dd);
    }
    function renderAttachment(a) {
        const el = document.createElement('div');
        el.className = 'is-d-att';
        el.dataset.action = 'openIssue';        // 첨부는 직접 링크를 외부로 연다
        el.dataset.url = a.url || '';
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
    function renderIssueDetail() {
        const d = isDocs[isIndex];
        const wrap = document.getElementById('is-detail-view');
        wrap.innerHTML = '';
        if (!d) { return; }

        const bucket = issueBucket(d);
        const stateCls = bucket;
        const stateLabel = (IS_STATE[bucket] || IS_STATE.open).label;
        const type = d.matchType || 'semantic';
        const badge = IS_BADGE[type] || IS_BADGE.semantic;
        const pct = (d.confidence != null) ? ' · ' + Math.round(d.confidence * 100) + '%' : '';

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
        nav.querySelector('.pos').textContent = (isIndex + 1) + ' / ' + isDocs.length;
        nav.querySelector('[data-action="issuePrev"]').disabled = isIndex === 0;
        nav.querySelector('[data-action="issueNext"]').disabled = isIndex >= isDocs.length - 1;
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

        // 라벨 칩 + 매치 신뢰도 배지
        const labelWrap = document.createElement('div');
        labelWrap.className = 'is-d-labels';
        (d.labels || []).forEach(name => {
            const chip = document.createElement('span');
            chip.className = 'is-d-label';
            chip.textContent = (String(name).charAt(0) === '#' ? '' : '#') + name;
            labelWrap.appendChild(chip);
        });
        const mb = document.createElement('span');
        mb.className = 'is-item__badge ' + badge.cls;
        mb.textContent = badge.label + pct;
        labelWrap.appendChild(mb);
        wrap.appendChild(labelWrap);

        // 메타 (담당자 / 연결된 코드 / 개설 / 업데이트)
        const meta = document.createElement('dl');
        meta.className = 'is-d-meta';
        const assignee = d.assignee || '';
        appendMetaRow(meta, '담당자', assignee ? ('@' + assignee) : '미지정', !!assignee);
        appendMetaRow(meta, '연결된 코드', isFileName || ('L' + (isLine || '?')), true);
        const created = isDateOnly(d.createdAt);
        const updated = isDateOnly(d.updatedAt);
        if (created) { appendMetaRow(meta, '개설', created, false); }
        if (updated) { appendMetaRow(meta, '업데이트', updated, false); }
        wrap.appendChild(meta);

        // 본문
        if (d.body) {
            const body = document.createElement('div');
            body.className = 'is-d-body';
            body.innerHTML = decorate(d.body);   // decorate 가 먼저 escape → 안전
            wrap.appendChild(body);
        }

        // 첨부파일
        const atts = d.attachments || [];
        if (atts.length) {
            const sec = document.createElement('div');
            sec.className = 'is-d-sec-title';
            sec.textContent = '📎 첨부파일 ' + atts.length;
            wrap.appendChild(sec);
            const list = document.createElement('div');
            list.className = 'is-d-atts';
            atts.forEach(a => list.appendChild(renderAttachment(a)));
            wrap.appendChild(list);
        }
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
    // blResult   : 캐럿 제거 + 출처/변경(PR 포함) 확정.
    let blExp = '';
    function blStreaming(p) {
        showTab('blame');
        document.getElementById('empty').classList.add('hidden');
        document.getElementById('info').classList.add('hidden');
        document.getElementById('content').classList.remove('hidden');
        revealTabs(true);

        // 콜아웃: "{작성자}님이 {월일}에 " 접두 + 타이핑될 설명(#ca-exp) + 깜빡이는 캐럿.
        blExp = p.text || '';
        const calloutEl = document.getElementById('narrative');
        if (p.author && p.dateShort) {
            calloutEl.innerHTML = '<span class="ca-author"></span>님이 ' + decorate(p.dateShort) + '에 <span id="ca-exp"></span><span class="caret"></span>';
            calloutEl.querySelector('.ca-author').textContent = p.author;
        } else {
            calloutEl.innerHTML = '<span id="ca-exp"></span><span class="caret"></span>';
        }
        if (blExp) { document.getElementById('ca-exp').innerHTML = renderBold(blExp); }

        // 메타/브레드크럼/이력 — render() 와 동일하게 채운다(출처/PR 은 done 에서 확정).
        document.getElementById('file-name').textContent = p.fileName;
        document.getElementById('file-line').textContent = 'L' + p.line;
        document.getElementById('file-kind').textContent = fileKind(p.fileName);
        document.getElementById('author-name').textContent = p.author || '?';
        toggle('author-team-wrap', !!p.team);
        if (p.team) document.getElementById('author-team').textContent = p.team;
        document.getElementById('meta-commit').textContent = p.commitShort || '—';
        toggle('meta-ticket-wrap', !!p.ticket);
        if (p.ticket) document.getElementById('meta-ticket').textContent = p.ticket;
        document.getElementById('meta-date').textContent = p.dateFull || '—';
        toggle('meta-relative-wrap', !!p.relative);
        if (p.relative) document.getElementById('meta-relative').textContent = p.relative;
        document.getElementById('meta-change').textContent = formatChange(p.changeStats, null) || '—';
        document.getElementById('meta-source').textContent = '…';

        const histWrap = document.getElementById('history-wrap');
        const histList = document.getElementById('history-list');
        histList.innerHTML = '';
        if (p.lineHistory && p.lineHistory.length) {
            histWrap.classList.remove('hidden');
            p.lineHistory.forEach(h => histList.appendChild(renderHistory(h, p.commitShort)));
        } else {
            histWrap.classList.add('hidden');
        }
        renderLineIssues(p.lineIssues);
    }
    function blDelta(delta) {
        blExp += delta;
        const exp = document.getElementById('ca-exp');
        if (exp) { exp.innerHTML = renderBold(blExp); }
    }
    function blResult(p) {
        blExp = (p.explanation || blExp || '').trim();
        const calloutEl = document.getElementById('narrative');
        const exp = document.getElementById('ca-exp');
        if (exp) {
            exp.innerHTML = renderBold(blExp);
            const caret = calloutEl.querySelector('.caret');
            if (caret) { caret.remove(); }
        } else {
            // 안전망: 콜아웃 구조가 없으면 설명만 통째로 그린다.
            calloutEl.innerHTML = decorate(blExp);
        }
        // 출처/변경(PR 라인 포함)을 최종 확정.
        document.getElementById('meta-source').textContent = p.sourceRef || '—';
        document.getElementById('meta-change').textContent = formatChange(p.changeStats, p.prInfo) || '—';
    }

    function render(p) {
        document.getElementById('empty').classList.add('hidden');
        document.getElementById('info').classList.add('hidden');
        document.getElementById('content').classList.remove('hidden');
        revealTabs(true);

        // 콜아웃 본문 — "{작성자}님이 {월일}에 {설명}" 한 문장. 작성자만 보라색 강조.
        // author/dateShort 가 없으면 설명만 노출한다.
        const calloutEl = document.getElementById('narrative');
        if (p.author && p.dateShort) {
            calloutEl.innerHTML = '<span class="ca-author"></span>님이 ' + decorate(p.dateShort) + '에 ' + decorate(p.explanation || '');
            calloutEl.querySelector('.ca-author').textContent = p.author;
        } else {
            calloutEl.innerHTML = decorate(p.explanation || '변경 사유를 분석할 수 없습니다.');
        }

        document.getElementById('file-name').textContent = p.fileName;
        document.getElementById('file-line').textContent = 'L' + p.line;
        document.getElementById('file-kind').textContent = fileKind(p.fileName);

        document.getElementById('author-name').textContent = p.author || '?';
        toggle('author-team-wrap', !!p.team);
        if (p.team) document.getElementById('author-team').textContent = p.team;

        document.getElementById('meta-commit').textContent = p.commitShort || '—';
        toggle('meta-ticket-wrap', !!p.ticket);
        if (p.ticket) document.getElementById('meta-ticket').textContent = p.ticket;

        document.getElementById('meta-date').textContent = p.dateFull || '—';
        toggle('meta-relative-wrap', !!p.relative);
        if (p.relative) document.getElementById('meta-relative').textContent = p.relative;

        const change = formatChange(p.changeStats, p.prInfo);
        document.getElementById('meta-change').textContent = change || '—';

        document.getElementById('meta-source').textContent = p.sourceRef || '—';

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
        renderLineIssues(p.lineIssues);
    }

    // 라인 수정 이력 한 줄 — 클릭하면 해당 커밋을 git show 로 연다.
    // currentShort(=블레임 대상 커밋 7자리)와 같은 커밋은 'current' 로 강조한다.
    function renderHistory(h, currentShort) {
        const short = (h.hash || '').slice(0, 7);
        const isCurrent = currentShort && short === currentShort;
        const el = document.createElement('div');
        el.className = 'hist-item' + (isCurrent ? ' current' : '');
        el.dataset.action = 'openCommitHash';
        el.dataset.hash = h.hash || '';
        // 배지는 자체 data-action 을 가져, 클릭 위임의 closest() 가 행(openCommitHash) 대신
        // 배지(openIssueTodo)를 먼저 잡는다 → 배지=이슈, 나머지 행=커밋 열기로 분기된다.
        const badge = (h.issueCount && h.issueCount > 0)
            ? '<span class="hist-item__issues" data-action="openIssueTodo" title="연관 이슈 보기 (준비 중)"><span class="ico">' + ICON.issue + '</span>이슈 ' + h.issueCount + '</span>'
            : '<span></span>';
        // 캐럿/이유 박스는 자체 data-action(expandHistory)을 가져, 클릭 위임의 closest() 가
        // 행(openCommitHash)보다 먼저 잡는다 → 캐럿=펼침, 배지=이슈, 나머지 행=커밋 열기로 분기.
        el.innerHTML =
            '<span class="hist-item__dot"></span>' +
            '<div style="min-width:0">' +
                '<div class="hist-item__head">' +
                    '<span class="hist-item__hash mono"></span>' +
                    '<span class="hist-item__date"></span>' +
                    '<span class="hist-item__caret" data-action="expandHistory" title="이 커밋의 변경 사유 보기">' + ICON.caret + '</span>' +
                '</div>' +
                '<div class="hist-item__subject"></div>' +
                '<div class="hist-item__author"></div>' +
                '<div class="hist-item__reason hidden" data-action="expandHistory"></div>' +
            '</div>' +
            badge;
        el.querySelector('.hist-item__hash').textContent = short;
        el.querySelector('.hist-item__date').textContent = formatHistDate(h.date);
        el.querySelector('.hist-item__subject').textContent = h.subject || '';
        el.querySelector('.hist-item__author').textContent = h.author || '';
        // 펼침/이유 요청이 자기 커밋 해시를 싣도록 캐럿·이유 박스에도 해시를 단다.
        el.querySelector('.hist-item__caret').dataset.hash = h.hash || '';
        el.querySelector('.hist-item__reason').dataset.hash = h.hash || '';
        return el;
    }

    // 상태 라벨 — 이슈 롤업 칩에 붙는다(현재/과거/되돌림).
    const LI_STATUS = { current: '현재', past: '과거', reverted: '되돌림' };

    // 연관 이슈 롤업 — 라인 전체에서 dedup 된 이슈 칩을 상태별 색으로 그린다.
    function renderLineIssues(list) {
        const wrap = document.getElementById('lineissues-wrap');
        const box = document.getElementById('lineissues-list');
        box.innerHTML = '';
        if (!list || !list.length) { wrap.classList.add('hidden'); return; }
        wrap.classList.remove('hidden');
        list.forEach(it => box.appendChild(renderLineIssue(it)));
    }
    function renderLineIssue(it) {
        const status = it.status || 'past';
        const el = document.createElement('span');
        el.className = 'li-chip li-chip--' + status;
        // URL 이 해석돼 있으면 외부 열기, 아니면 임시 안내(이슈 기능 연동 전).
        el.dataset.action = it.url ? 'openIssue' : 'openIssueTodo';
        if (it.url) { el.dataset.url = it.url; }
        const count = (it.changeCount && it.changeCount > 1) ? (' · ' + it.changeCount + '회') : '';
        el.innerHTML =
            '<span class="ico">' + ICON.issue + '</span>' +
            '<span class="li-chip__num"></span>' +
            '<span class="li-chip__status"></span>';
        el.querySelector('.li-chip__num').textContent = '#' + it.number;
        el.querySelector('.li-chip__status').textContent = (LI_STATUS[status] || '') + count;
        el.title = it.title ? ('#' + it.number + ' ' + it.title) : ('이슈 #' + it.number);
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

    function formatChange(stats, pr) {
        const parts = [];
        if (stats) parts.push('+' + stats.added + ' -' + stats.removed);
        if (pr) parts.push('동일 PR ' + pr.lines + ' 라인');
        return parts.join(' · ');
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
        // 라인 수정 이력 항목은 자기 커밋 해시를 함께 실어 보낸다.
        if (el.dataset.action === 'openCommitHash') {
            vscode.postMessage({ type: 'openCommitHash', payload: { hash: el.dataset.hash } });
            return;
        }
        // 이슈 목록 항목 선택 — 외부로 열지 않고 상세 화면으로 전환한다.
        if (el.dataset.action === 'openIssueDetail') {
            openIssueDetail(parseInt(el.dataset.index, 10) || 0);
            return;
        }
        // 상세 화면 네비게이션
        if (el.dataset.action === 'issueBack') { showIssueList(); return; }
        if (el.dataset.action === 'issuePrev') { openIssueDetail(isIndex - 1); return; }
        if (el.dataset.action === 'issueNext') { openIssueDetail(isIndex + 1); return; }
        // AI 질문 — 아직 미연동, '준비 중' 안내만(openIssueTodo 재사용).
        if (el.dataset.action === 'issueAiAsk') {
            vscode.postMessage({ type: 'openIssueTodo' });
            return;
        }
        // 이슈/첨부 항목은 자기 URL 을 함께 실어 외부로 연다.
        if (el.dataset.action === 'openIssue') {
            vscode.postMessage({ type: 'openIssue', payload: { url: el.dataset.url } });
            return;
        }
        // 라인 수정 이력의 '이슈 N' 배지 — 이슈 기능 미완이라 임시 안내만(행 클릭으로 번지지 않음).
        if (el.dataset.action === 'openIssueTodo') {
            vscode.postMessage({ type: 'openIssueTodo' });
            return;
        }
        vscode.postMessage({ type: el.dataset.action });
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
