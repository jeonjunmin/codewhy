import * as vscode from 'vscode';
import { EditorContext } from '../../shared/editor';
import { TimelineResult } from '../../shared/types';

export const VIEW_ID = 'codewhy.timelineSummary';

const BADGE_COLORS = [
    '#e05454', '#2cb8b8', '#8b5cf6', '#d97706', '#16a34a',
    '#3b82f6', '#ec4899', '#f97316', '#14b8a6', '#a855f7',
];
const KO_MONTHS = ['1월','2월','3월','4월','5월','6월','7월','8월','9월','10월','11월','12월'];

function esc(t: unknown): string {
    return String(t ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

function renderBold(t: string): string {
    return esc(t).replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
}

function getMonth(dateStr: string): string {
    const m = parseInt((dateStr || '').split('-')[1], 10);
    return KO_MONTHS[m - 1] || '';
}

function splitDesc(desc: string): { title: string; body: string } {
    const s = (desc || '').trim();
    const idx = s.search(/[.。,，\n]/);
    if (idx > 0 && idx < 30) {
        return { title: s.slice(0, idx).trim(), body: s.slice(idx + 1).trim() };
    }
    return { title: s, body: '' };
}

type State =
    | { kind: 'empty' }
    | { kind: 'streaming'; ctx: EditorContext; fileName: string; text: string }
    | { kind: 'result'; ctx: EditorContext; result: TimelineResult };

export class TimelineSidebarProvider implements vscode.WebviewViewProvider {
    private view?: vscode.WebviewView;
    private state: State = { kind: 'empty' };

    constructor(private readonly extensionUri: vscode.Uri) {}

    resolveWebviewView(view: vscode.WebviewView) {
        this.view = view;
        view.webview.options = {
            enableScripts: false,
            localResourceRoots: [this.extensionUri],
        };
        this._render();
    }

    setTimeline(ctx: EditorContext, result: TimelineResult) {
        this.state = { kind: 'result', ctx, result };
        if (!this.view) {
            vscode.commands.executeCommand(`${VIEW_ID}.focus`);
            return;
        }
        this._render();
    }

    startStreaming(ctx: EditorContext) {
        const fileName = ctx.filePath.split(/[\\/]/).pop() ?? ctx.filePath;
        this.state = { kind: 'streaming', ctx, fileName, text: '' };
        if (!this.view) {
            vscode.commands.executeCommand(`${VIEW_ID}.focus`);
            return;
        }
        this._render();
    }

    appendStreamDelta(delta: string) {
        if (this.state.kind !== 'streaming') { return; }
        this.state = { ...this.state, text: this.state.text + delta };
        this._render();
    }

    showEmpty() {
        this.state = { kind: 'empty' };
        if (this.view) { this._render(); }
    }

    private _render() {
        if (!this.view) { return; }
        this.view.webview.html = this._buildHtml();
    }

    private _buildHtml(): string {
        const css = this._css();
        const body = this._body();
        return `<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline';">
<style>${css}</style>
</head>
<body>${body}</body>
</html>`;
    }

    private _body(): string {
        const s = this.state;

        if (s.kind === 'empty') {
            return `<div class="empty"><div class="empty-icon">⏱</div><div>파일을 우클릭 후<br><strong>이 파일의 역사 요약</strong>을 선택하세요.</div></div>`;
        }

        if (s.kind === 'streaming') {
            if (!s.text) {
                return `<div class="scroll"><div class="loading"><div class="spinner"></div><span>AI가 소스 코드를 분석 중입니다...</span></div></div>`;
            }
            return `<div class="scroll">
<div class="file-chip"><span>📄</span><span class="file-chip-name">${esc(s.fileName)}</span></div>
<div class="ai-card">
  <div class="ai-label"><span class="ai-label-icon">✳</span> AI 요약</div>
  <div class="ai-text">${renderBold(s.text)}<span class="caret"></span></div>
</div>
</div>`;
        }

        // result
        const { ctx, result } = s;
        const fileName = ctx.filePath.split(/[\\/]/).pop() ?? ctx.filePath;
        const sorted = [...(result.milestones ?? [])].sort((a, b) => a.date.localeCompare(b.date));

        const milestoneHtml = sorted.length === 0 ? '' : `
<div class="section-hd">
  <span class="section-hd-label">주요 마일스톤</span>
  <span class="section-hd-count">${sorted.length}건</span>
  <div class="section-hd-line"></div>
</div>
<div class="timeline">
${sorted.map((m, i) => {
    const color = BADGE_COLORS[i % BADGE_COLORS.length];
    const month = getMonth(m.date);
    const { title, body } = splitDesc(m.description);
    const monthHtml = month
        ? `<span class="tl-month">${esc(month)}</span><span class="tl-dot-sep">·</span>`
        : '';
    return `<div class="tl-item">
  <div class="tl-left">
    <div class="tl-badge" style="background:${color}">${i + 1}</div>
    <div class="tl-line"></div>
  </div>
  <div class="tl-right">
    <div class="tl-date">${monthHtml}<span>${esc(m.date)}</span></div>
    <div class="tl-title">${esc(title)}</div>
    ${body ? `<div class="tl-desc">${esc(body)}</div>` : ''}
  </div>
</div>`;
}).join('\n')}
</div>`;

        return `<div class="scroll">
<div class="file-chip"><span>📄</span><span class="file-chip-name">${esc(fileName)}</span></div>
<div class="ai-card">
  <div class="ai-label"><span class="ai-label-icon">✳</span> AI 요약</div>
  <div class="ai-text">${renderBold(result.summary ?? '')}</div>
</div>
${milestoneHtml}
</div>`;
    }

    private _css(): string {
        return `
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: var(--vscode-font-family);
  font-size: 13px;
  color: var(--vscode-editor-foreground);
  background: var(--vscode-sideBar-background, var(--vscode-editor-background));
  height: 100vh;
  overflow: hidden;
}
.scroll {
  height: 100vh;
  overflow-y: auto;
  padding: 12px 14px 24px;
}
.scroll::-webkit-scrollbar { width: 4px; }
.scroll::-webkit-scrollbar-thumb {
  background: var(--vscode-scrollbarSlider-background);
  border-radius: 2px;
}
.empty {
  display: flex; flex-direction: column; align-items: center; justify-content: center;
  gap: 10px; height: 220px;
  color: var(--vscode-descriptionForeground);
  font-size: 12px; text-align: center; padding: 0 20px;
}
.empty-icon { font-size: 28px; opacity: 0.35; }
.loading {
  display: flex; align-items: center; gap: 9px;
  color: var(--vscode-descriptionForeground);
  font-size: 12px; padding: 20px 0;
}
.spinner {
  flex-shrink: 0; width: 15px; height: 15px;
  border: 2px solid var(--vscode-widget-border);
  border-top-color: var(--vscode-progressBar-background);
  border-radius: 50%;
  animation: spin .75s linear infinite;
}
@keyframes spin { to { transform: rotate(360deg); } }
.file-chip {
  display: inline-flex; align-items: center; gap: 5px;
  font-size: 11px;
  color: var(--vscode-descriptionForeground);
  background: var(--vscode-badge-background);
  border-radius: 4px;
  padding: 3px 8px;
  margin-bottom: 14px;
  max-width: 100%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.file-chip-name { font-weight: 600; color: var(--vscode-editor-foreground); }
.ai-card {
  background: color-mix(in srgb, var(--vscode-editor-background) 60%, #6c5ce7 40%);
  border: 1px solid rgba(108, 92, 231, 0.35);
  border-radius: 10px;
  padding: 13px 15px 15px;
  margin-bottom: 18px;
}
.ai-label {
  display: flex; align-items: center; gap: 6px;
  font-size: 11px; font-weight: 700; letter-spacing: .06em;
  color: #a29bfe;
  margin-bottom: 9px;
}
.ai-label-icon { font-size: 13px; }
.ai-text {
  font-size: 12.5px; line-height: 1.75;
  color: var(--vscode-editor-foreground);
  word-break: keep-all;
}
.ai-text strong { color: #fff; font-weight: 700; }
.caret {
  display: inline-block;
  width: 2px; height: 1em;
  margin-left: 2px;
  vertical-align: text-bottom;
  background: var(--vscode-editor-foreground);
  animation: caret-blink 0.9s steps(1) infinite;
}
@keyframes caret-blink { 0%, 49% { opacity: 1; } 50%, 100% { opacity: 0; } }
.section-hd {
  display: flex; align-items: center; gap: 8px;
  margin-bottom: 14px;
}
.section-hd-label {
  font-size: 10.5px; font-weight: 700; letter-spacing: .1em; text-transform: uppercase;
  color: var(--vscode-descriptionForeground);
  white-space: nowrap;
}
.section-hd-count {
  font-size: 10.5px; padding: 1px 7px; border-radius: 8px;
  background: var(--vscode-badge-background);
  color: var(--vscode-badge-foreground);
}
.section-hd-line { flex: 1; height: 1px; background: var(--vscode-widget-border); }
.timeline { position: relative; }
.tl-item { display: flex; gap: 12px; position: relative; margin-bottom: 0; }
.tl-left {
  display: flex; flex-direction: column; align-items: center;
  flex-shrink: 0; width: 26px;
}
.tl-badge {
  width: 26px; height: 26px; border-radius: 50%;
  display: flex; align-items: center; justify-content: center;
  font-size: 11px; font-weight: 700; color: #fff;
  flex-shrink: 0;
}
.tl-line {
  width: 2px; flex: 1; min-height: 12px;
  background: var(--vscode-widget-border);
  margin: 4px 0;
}
.tl-item:last-child .tl-line { display: none; }
.tl-right { padding-bottom: 20px; flex: 1; min-width: 0; }
.tl-item:last-child .tl-right { padding-bottom: 0; }
.tl-date {
  font-size: 11px; color: var(--vscode-descriptionForeground);
  margin-bottom: 5px; margin-top: 3px;
  display: flex; align-items: center; gap: 5px;
}
.tl-month { font-weight: 700; color: var(--vscode-editor-foreground); }
.tl-dot-sep { opacity: .4; }
.tl-title {
  font-size: 12.5px; font-weight: 700;
  line-height: 1.4;
  color: var(--vscode-editor-foreground);
  margin-bottom: 4px;
  word-break: keep-all;
}
.tl-desc {
  font-size: 11.5px; line-height: 1.6;
  color: var(--vscode-descriptionForeground);
  word-break: keep-all;
}`;
    }
}
