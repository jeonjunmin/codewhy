import * as vscode from 'vscode';
import { EditorContext } from '../../shared/editor';
import { BlameResult, RelatedChange } from '../../shared/types';

/**
 * Context Blame 사이드바 — 첨부 디자인의 "CONTEXT BLAME" 패널을 그대로 구현.
 *
 * 구성 (위에서 아래):
 *   ┌ 헤더 (✨ CONTEXT BLAME + 핀/설정 아이콘)
 *   ├ ✨ 이 라인이 추가된 이유 — 보라색 콜아웃
 *   ├ 파일 브레드크럼 (PaymentService.kt › L7)
 *   ├ 메타 테이블 (작성자/커밋/날짜/변경/출처)
 *   ├ 이 변경과 함께 일어난 일 — RelatedChange 목록
 *   ├ AI 에게 더 묻기 — 인풋
 *   └ 기획서 §X.X 열기 — Primary CTA + 보조 액션 ⋯ ⌥
 *
 * 라이프사이클:
 *   - WebviewView 는 활성화될 때 한 번 resolve 된다.
 *   - 라인 변경마다 setBlame() 으로 postMessage 만 보낸다 — HTML 은 다시 안 그린다.
 *
 * 👤 담당: 개발자 A
 */

export const VIEW_ID = 'codewhy.contextBlame';

export class ContextBlameSidebarProvider implements vscode.WebviewViewProvider {
    private view?: vscode.WebviewView;
    private last?: { ctx: EditorContext; result: BlameResult; pinned: boolean };

    constructor(
        private readonly extensionUri: vscode.Uri,
        private readonly handlers: {
            onOpenSpec: (sourceRef: string | null) => void;
            onOpenCommit: (commitHash: string, repoPath: string) => void;
            onOpenHistory: () => void;
            onTogglePin: (filePath: string, line: number) => void;
            onAskAi: (question: string, ctx: EditorContext, result: BlameResult) => void;
            onCopy: (text: string) => void;
        },
    ) {}

    resolveWebviewView(view: vscode.WebviewView) {
        this.view = view;
        view.webview.options = { enableScripts: true, localResourceRoots: [this.extensionUri] };
        view.webview.html = this.renderHtml();

        view.webview.onDidReceiveMessage(msg => this.handleMessage(msg));

        // 사이드바를 다시 열었을 때 마지막 분석 결과 복원
        if (this.last) {
            this.postRender(this.last.ctx, this.last.result, this.last.pinned);
        } else {
            this.postEmpty();
        }
    }

    /** Context Blame 분석이 끝났을 때 view.ts 에서 호출. */
    setBlame(ctx: EditorContext, result: BlameResult, pinned: boolean) {
        this.last = { ctx, result, pinned };
        if (!this.view) {
            // 사이드바가 아직 안 열려 있으면 강제로 표시한다 — 첫 분석 시 자연스럽게 펼쳐짐
            vscode.commands.executeCommand(`${VIEW_ID}.focus`);
            return;
        }
        this.postRender(ctx, result, pinned);
        this.view.show?.(true);
    }

    /** 핀 토글 시 그라데이션·아이콘만 갱신. */
    refreshPinned(pinned: boolean) {
        if (!this.last || !this.view) { return; }
        this.last.pinned = pinned;
        this.view.webview.postMessage({ type: 'pinned', pinned });
    }

    /** "AI에게 더 묻기" 답변을 질문 버블과 함께 표시. answer='…' 면 로딩 상태. */
    showAnswer(question: string, answer: string) {
        this.view?.webview.postMessage({ type: 'answer', payload: { question, answer } });
    }

    // ─── 메시지 라우팅 ────────────────────────────────────────────────────
    private handleMessage(msg: { type: string; payload?: any }) {
        if (!this.last) { return; }
        const { ctx, result } = this.last;
        switch (msg.type) {
            case 'openSpec':
                this.handlers.onOpenSpec(result.sourceRef ?? result.specRef ?? null);
                break;
            case 'openCommit':
                this.handlers.onOpenCommit(result.commitHash, ctx.repoPath);
                break;
            case 'openHistory':
                this.handlers.onOpenHistory();
                break;
            case 'togglePin':
                this.handlers.onTogglePin(ctx.filePath, ctx.line);
                break;
            case 'askAi':
                if (typeof msg.payload?.question === 'string' && msg.payload.question.trim()) {
                    this.handlers.onAskAi(msg.payload.question.trim(), ctx, result);
                }
                break;
            case 'copy':
                this.handlers.onCopy(plainTextOf(ctx, result));
                break;
        }
    }

    // ─── postMessage 헬퍼 ─────────────────────────────────────────────────
    private postEmpty() {
        this.view?.webview.postMessage({ type: 'empty' });
    }

    private postRender(ctx: EditorContext, r: BlameResult, pinned: boolean) {
        const fileName = ctx.filePath.split(/[\\/]/).pop() ?? ctx.filePath;
        const payload = {
            fileName,
            line: ctx.line,
            narrative: formatNarrative(r),
            author: r.author,
            team: r.team,
            commitShort: (r.commitHash || '').slice(0, 7),
            ticket: r.ticket,
            date: formatDisplayDate(r.date),
            relative: formatRelativeKo(r.date),
            changeStats: r.changeStats,
            prInfo: r.prInfo,
            sourceRef: r.sourceRef ?? r.specRef ?? null,
            specRef: r.specRef,
            related: r.relatedChanges ?? [],
            aiSuggestion: r.aiSuggestion ?? null,
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

    /* ── 헤더 ────────────────────────────────────────────────────── */
    .head {
        display: flex; align-items: center; justify-content: space-between;
        padding: 12px 14px 8px;
        border-bottom: 1px solid var(--line);
    }
    .head__title {
        display: inline-flex; align-items: center; gap: 6px;
        font-size: 11px; font-weight: 700; letter-spacing: 0.12em;
        color: var(--fg-dim); text-transform: uppercase;
    }
    .head__title .spark { color: var(--accent-violet); display: inline-flex; }
    .head__actions { display: flex; gap: 2px; }
    .icon-btn {
        background: transparent; border: none; cursor: pointer;
        color: var(--fg-mute); padding: 3px; border-radius: 5px;
        display: inline-flex; align-items: center; justify-content: center;
        transition: background 0.12s, color 0.12s;
    }
    .icon-btn:hover { background: var(--line); color: var(--fg); }
    .icon-btn.active { color: var(--accent-violet); }

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
    .meta .avatar {
        width: 18px; height: 18px; border-radius: 50%;
        background: #DC2626; color: #fff;
        display: inline-flex; align-items: center; justify-content: center;
        font-size: 10px; font-weight: 700;
    }
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
    .rel-item {
        display: flex; gap: 10px; align-items: flex-start;
        padding: 8px 10px;
        background: var(--surface);
        border: 1px solid var(--line);
        border-radius: 8px;
    }
    .rel-item__icon {
        width: 24px; height: 24px; flex-shrink: 0;
        display: inline-flex; align-items: center; justify-content: center;
        background: var(--line); border-radius: 6px;
        color: var(--fg-dim);
    }
    .rel-item__text { min-width: 0; }
    .rel-item__title { color: var(--fg); font-size: 12px; font-weight: 500; }
    .rel-item__meta { color: var(--fg-mute); font-size: 11px; margin-top: 2px; }

    /* ── AI 에게 더 묻기 ─────────────────────────────────────────── */
    .ask {
        display: flex; align-items: center; gap: 8px;
        padding: 9px 12px;
        background: var(--surface);
        border: 1px solid var(--line);
        border-radius: 9px;
    }
    .ask__icon { color: var(--accent-violet); display: inline-flex; flex-shrink: 0; }
    .ask__label { color: var(--fg-mute); font-size: 11.5px; flex-shrink: 0; }
    .ask__input {
        flex: 1; min-width: 0;
        background: transparent; border: none; outline: none;
        color: var(--fg); font-size: 12px;
        font-family: inherit;
    }
    .ask__input::placeholder { color: var(--fg-mute); font-style: italic; }

    /* ── AI 질문/답변 스레드 ─────────────────────────────────────── */
    .ask-thread { display: flex; flex-direction: column; gap: 8px; }
    .qa { display: flex; flex-direction: column; gap: 4px; }
    .qa__q {
        align-self: flex-end; max-width: 90%;
        background: var(--line); color: var(--fg);
        padding: 6px 10px; border-radius: 9px 9px 2px 9px;
        font-size: 12px;
    }
    .qa__a {
        align-self: flex-start; max-width: 95%;
        background: var(--callout-bg); color: var(--fg);
        border: 1px solid var(--line-soft);
        padding: 8px 11px; border-radius: 9px 9px 9px 2px;
        font-size: 12.5px; line-height: 1.55;
    }
    .qa__a code {
        background: var(--code-bg); color: var(--code-fg);
        padding: 1px 5px; border-radius: 4px; font-size: 11.5px;
    }
    .qa__a.loading { color: var(--fg-mute); font-style: italic; }

    /* ── 푸터: CTA + 보조 액션 ───────────────────────────────────── */
    .footer { display: flex; gap: 6px; align-items: stretch; }
    .cta {
        flex: 1;
        display: inline-flex; align-items: center; gap: 8px; justify-content: flex-start;
        padding: 10px 12px;
        background: var(--grad);
        color: #fff; border: none; border-radius: 9px;
        font-size: 12.5px; font-weight: 600;
        cursor: pointer; font-family: inherit;
        transition: filter 0.12s;
    }
    .cta:hover { filter: brightness(1.12); }
    .ghost-btn {
        background: var(--surface); border: 1px solid var(--line);
        color: var(--fg-dim);
        padding: 0 10px; border-radius: 9px;
        cursor: pointer;
        display: inline-flex; align-items: center; justify-content: center;
        transition: background 0.12s, color 0.12s;
    }
    .ghost-btn:hover { background: var(--line); color: var(--fg); }

    /* ── 작은 보조 ───────────────────────────────────────────────── */
    .hidden { display: none !important; }
    .ai-suggest {
        background: var(--surface-2);
        border: 1px solid var(--line);
        border-radius: 9px;
        padding: 9px 11px;
        font-size: 11.5px; color: #D4D4D8;
    }
    .ai-suggest__title {
        display: inline-flex; align-items: center; gap: 5px;
        color: var(--accent-cyan); font-weight: 600;
        margin-bottom: 4px;
    }
</style>
</head>
<body>
    <div class="head">
        <div class="head__title">
            <span class="spark" id="ico-spark"></span> CONTEXT BLAME
        </div>
        <div class="head__actions">
            <button class="icon-btn" id="btn-pin" data-action="togglePin" title="이 라인 고정"></button>
            <button class="icon-btn" id="btn-copy" data-action="copy" title="내용 복사"></button>
        </div>
    </div>

    <div id="empty" class="empty">
        에디터에서 라인을 선택하고 <span class="kbd">🔍 왜 바꿨어?</span> 렌즈를 클릭하면<br/>이곳에 변경 사유가 나타납니다.
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
            <dt>작성자</dt><dd class="author"><span class="avatar" id="author-initial"></span><strong id="author-name"></strong><span id="author-team-wrap" class="hidden">&nbsp;·&nbsp;<span id="author-team"></span></span></dd>
            <dt>커밋</dt><dd><a class="link mono" id="meta-commit" data-action="openCommit"></a><span id="meta-ticket-wrap" class="hidden"> — <span class="ticket" id="meta-ticket"></span></span></dd>
            <dt>날짜</dt><dd><span id="meta-date"></span><span id="meta-relative-wrap" class="hidden"> <span style="color:var(--fg-mute)">(<span id="meta-relative"></span>)</span></span></dd>
            <dt>변경</dt><dd id="meta-change">—</dd>
            <dt>출처</dt><dd id="meta-source">—</dd>
        </dl>

        <section id="related-wrap" class="hidden">
            <div class="related__title">이 변경과 함께 일어난 일</div>
            <div class="related__list" id="related-list"></div>
        </section>

        <div id="ai-suggest" class="ai-suggest hidden">
            <div class="ai-suggest__title"><span id="ico-ai"></span> AI 추론</div>
            <div id="ai-suggest-body"></div>
        </div>

        <div class="ask">
            <span class="ask__icon" id="ico-ask"></span>
            <span class="ask__label">AI에게 더 묻기 —</span>
            <input class="ask__input" id="ask-input" type="text" placeholder='"왜 4%가 아닌 3%일까?"' />
        </div>

        <div id="ask-thread" class="ask-thread hidden"></div>

        <div class="footer">
            <button class="cta" data-action="openSpec">
                <span id="ico-cta"></span><span id="cta-label">기획서 열기</span>
            </button>
            <button class="ghost-btn" data-action="openCommit" title="커밋 보기" id="ico-commit-btn"></button>
            <button class="ghost-btn" data-action="openHistory" title="히스토리">⋯</button>
        </div>
    </div>

<script nonce="${nonce}">
    const vscode = acquireVsCodeApi();

    // ── 아이콘들 (인라인 SVG) ───────────────────────────────────────
    const ICON = {
        spark:  '<svg width="13" height="13" viewBox="0 0 16 16" fill="currentColor"><path d="M8 1l1.5 4.5L14 7l-4.5 1.5L8 13l-1.5-4.5L2 7l4.5-1.5L8 1z"/></svg>',
        pinOff: '<svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.3"><path d="M10 1l5 5-2 2-1.5-.5L8 11l-.5 1.5L2 14l1.5-5.5L5 8l3.5-3.5L8 3l2-2z"/></svg>',
        pinOn:  '<svg width="14" height="14" viewBox="0 0 16 16" fill="currentColor"><path d="M10 1l5 5-2 2-1.5-.5L8 11l-.5 1.5L2 14l1.5-5.5L5 8l3.5-3.5L8 3l2-2z"/></svg>',
        copy:   '<svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.3"><rect x="5" y="5" width="9" height="9" rx="1.5"/><path d="M2 11V3.5A1.5 1.5 0 0 1 3.5 2H11"/></svg>',
        doc:    '<svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.2"><path d="M3 1h7l3 3v11H3V1z"/><path d="M10 1v4h3M5 8h6M5 10h6M5 12h4"/></svg>',
        branch: '<svg width="13" height="13" viewBox="0 0 16 16" fill="currentColor"><path d="M5 3a1.5 1.5 0 1 0-2 0v8a1.5 1.5 0 1 0 1 0V8h3a3 3 0 0 0 3-3V4.92a1.5 1.5 0 1 0-1 0V5a2 2 0 0 1-2 2H4V3z"/></svg>',
        shield: '<svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.2"><path d="M8 1l6 2v5c0 4-2.8 6.6-6 7-3.2-.4-6-3-6-7V3l6-2z"/></svg>',
        commit: '<svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.3"><circle cx="8" cy="8" r="2.5"/><path d="M0 8h5M11 8h5"/></svg>',
    };
    document.getElementById('ico-spark').innerHTML = ICON.spark;
    document.getElementById('ico-callout').innerHTML = ICON.spark;
    document.getElementById('ico-ai').innerHTML = ICON.spark;
    document.getElementById('ico-ask').innerHTML = ICON.spark;
    document.getElementById('ico-cta').innerHTML = ICON.doc;
    document.getElementById('ico-commit-btn').innerHTML = ICON.branch;
    setPin(false);

    // ── 메시지 수신 ────────────────────────────────────────────────
    window.addEventListener('message', (e) => {
        const msg = e.data;
        if (msg.type === 'render') {
            render(msg.payload);
        } else if (msg.type === 'empty') {
            document.getElementById('empty').classList.remove('hidden');
            document.getElementById('content').classList.add('hidden');
        } else if (msg.type === 'pinned') {
            setPin(msg.pinned);
        } else if (msg.type === 'answer') {
            renderAnswer(msg.payload);
        }
    });

    let pendingAnswerEl = null;
    function renderAnswer(p) {
        const thread = document.getElementById('ask-thread');
        thread.classList.remove('hidden');
        const loading = p.answer === '…';
        if (loading || !pendingAnswerEl) {
            // 새 질문 → 질문/답변 버블 한 쌍 추가
            const qa = document.createElement('div');
            qa.className = 'qa';
            const q = document.createElement('div');
            q.className = 'qa__q';
            q.textContent = p.question;
            const a = document.createElement('div');
            a.className = 'qa__a' + (loading ? ' loading' : '');
            if (loading) { a.textContent = '답변 작성 중…'; } else { a.innerHTML = decorate(p.answer); }
            qa.appendChild(q);
            qa.appendChild(a);
            thread.appendChild(qa);
            pendingAnswerEl = loading ? a : null;
        } else {
            // 직전 로딩 버블을 실제 답변으로 교체
            pendingAnswerEl.classList.remove('loading');
            pendingAnswerEl.innerHTML = decorate(p.answer);
            pendingAnswerEl = null;
        }
        thread.lastElementChild.scrollIntoView({ block: 'nearest' });
    }

    function setPin(pinned) {
        const btn = document.getElementById('btn-pin');
        btn.innerHTML = pinned ? ICON.pinOn : ICON.pinOff;
        btn.classList.toggle('active', !!pinned);
    }

    function render(p) {
        document.getElementById('empty').classList.add('hidden');
        document.getElementById('content').classList.remove('hidden');

        document.getElementById('narrative').innerHTML = decorate(p.narrative);
        document.getElementById('file-name').textContent = p.fileName;
        document.getElementById('file-line').textContent = 'L' + p.line;
        document.getElementById('file-kind').textContent = fileKind(p.fileName);

        const initial = (p.author || '?').charAt(0);
        document.getElementById('author-initial').textContent = initial;
        document.getElementById('author-name').textContent = p.author || '?';
        toggle('author-team-wrap', !!p.team);
        if (p.team) document.getElementById('author-team').textContent = p.team;

        document.getElementById('meta-commit').textContent = p.commitShort || '—';
        toggle('meta-ticket-wrap', !!p.ticket);
        if (p.ticket) document.getElementById('meta-ticket').textContent = p.ticket;

        document.getElementById('meta-date').textContent = p.date || '—';
        toggle('meta-relative-wrap', !!p.relative);
        if (p.relative) document.getElementById('meta-relative').textContent = p.relative;

        const change = formatChange(p.changeStats, p.prInfo);
        document.getElementById('meta-change').textContent = change || '—';

        document.getElementById('meta-source').textContent = p.sourceRef || '—';

        // related
        const wrap = document.getElementById('related-wrap');
        const list = document.getElementById('related-list');
        list.innerHTML = '';
        if (p.related && p.related.length) {
            wrap.classList.remove('hidden');
            p.related.forEach(r => list.appendChild(renderRelated(r)));
        } else {
            wrap.classList.add('hidden');
        }

        // ai suggestion
        const aiWrap = document.getElementById('ai-suggest');
        if (p.aiSuggestion) {
            aiWrap.classList.remove('hidden');
            document.getElementById('ai-suggest-body').textContent = p.aiSuggestion;
        } else {
            aiWrap.classList.add('hidden');
        }

        // cta label
        document.getElementById('cta-label').textContent = (p.specRef || '기획서') + ' 열기';

        // 라인이 바뀌면 이전 Q&A 스레드는 초기화
        const thread = document.getElementById('ask-thread');
        thread.innerHTML = '';
        thread.classList.add('hidden');
        pendingAnswerEl = null;

        setPin(!!p.pinned);
    }

    function renderRelated(r) {
        const el = document.createElement('div');
        el.className = 'rel-item';
        const icon = r.kind === 'doc' ? ICON.doc : r.kind === 'branch' ? ICON.branch : r.kind === 'security' ? ICON.shield : ICON.commit;
        el.innerHTML =
            '<span class="rel-item__icon">' + icon + '</span>' +
            '<div class="rel-item__text">' +
                '<div class="rel-item__title"></div>' +
                '<div class="rel-item__meta"></div>' +
            '</div>';
        el.querySelector('.rel-item__title').textContent = r.title;
        el.querySelector('.rel-item__meta').textContent = r.meta;
        return el;
    }

    function decorate(s) {
        // "..." 인용은 코드 강조로 변환
        const esc = String(s)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
        return esc.replace(/"([^"]+)"/g, '<code>$1</code>');
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

    // ── 인풋: Enter 누르면 AI 질문 발사 ───────────────────────────
    document.getElementById('ask-input').addEventListener('keydown', (e) => {
        if (e.key === 'Enter') {
            const v = e.currentTarget.value;
            if (v.trim()) {
                vscode.postMessage({ type: 'askAi', payload: { question: v } });
                e.currentTarget.value = '';
            }
        }
    });

    // ── 버튼·링크 액션 위임 ───────────────────────────────────────
    document.body.addEventListener('click', (e) => {
        const el = e.target.closest('[data-action]');
        if (!el) return;
        vscode.postMessage({ type: el.dataset.action });
    });
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

function plainTextOf(ctx: EditorContext, r: BlameResult): string {
    const file = ctx.filePath.split(/[\\/]/).pop() ?? ctx.filePath;
    return [
        `[CodeWhy] ${file} : L${ctx.line}`,
        formatNarrative(r),
        r.commitHash ? `commit: ${r.commitHash.slice(0, 7)}` : '',
        r.ticket ? `ticket: ${r.ticket}` : '',
        r.specRef ? `spec: ${r.specRef}` : '',
        r.team ? `team: ${r.team}` : '',
        r.aiSuggestion ? `AI 추론: ${r.aiSuggestion}` : '',
    ].filter(Boolean).join('\n');
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

// ─────────────────────────────────────────────────────────────────────────────
// 콜아웃 본문 — "홍길동님이 3월 15일에 \"…\" 기획 내용을 반영하기 위해 추가했습니다."
//
// 이미 갖춰진 것(중복 작업 불필요):
//   · 작성자 조사 "님이" 고정 — "님"(받침 ㅁ) 뒤는 항상 "이" 라 분기 불필요.
//   · 인용 "..." → <code> 강조 : 웹뷰 decorate() 가 처리하므로 따옴표를 그대로 통과시키면 됨.
//   · 연도 생략 한국식 날짜      : formatDisplayDate() 가 "3월 15일" 형태로 반환.
//   · josa() 헬퍼는 목적어 조사("을/를" 등) 분기에 필요하면 쓸 수 있게 남겨둠.
//
// TODO(개발자 A) — 아래 본문 한 문장을 시안 톤으로 다듬어주세요. 남은 확인 포인트 2가지:
//   (a) "…에 {explanation}" 연결 톤 — explanation 은 이미 완결된 한 문장이라
//       "3월 15일에 " 뒤에 그대로 붙으면 어색할 수 있음. 연결 표현/어미를 다듬을 것.
//   (b) explanation 에 인용 "..." 이 없는 경우에도 한 문장으로 매끄럽게 흐르는지 확인.
// ─────────────────────────────────────────────────────────────────────────────
export function formatNarrative(r: BlameResult): string {
    return `${r.author}님이 ${formatDisplayDate(r.date)}에 ${r.explanation}`;
}

function formatDisplayDate(s: string): string {
    const d = parseDateLoose(s);
    if (!d) { return s; }
    return `${d.getMonth() + 1}월 ${d.getDate()}일`;
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
