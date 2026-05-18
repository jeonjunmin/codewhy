import * as vscode from 'vscode';
import { EditorContext } from '../../shared/editor';
import { TimelineMilestone, TimelineResult } from '../../shared/types';

/**
 * Timeline Summary 결과를 Webview 패널에 표시한다.
 *
 * 👤 담당: 개발자 B
 */
export function showTimelineSummaryView(
    ctx: EditorContext,
    result: TimelineResult
) {
    const fileName = ctx.filePath.split(/[\\/]/).pop() ?? ctx.filePath;

    const panel = vscode.window.createWebviewPanel(
        'codewhyTimeline',
        `CodeWhy: ${fileName}`,
        vscode.ViewColumn.Beside,
        { enableScripts: false }
    );

    panel.webview.html = buildHtml(fileName, ctx.filePath, result);
}

// ── HTML 빌더 ──────────────────────────────────────────────────────────────

function buildHtml(fileName: string, fullPath: string, result: TimelineResult): string {
    const sorted = result.milestones
        .slice()
        .sort((a, b) => a.date.localeCompare(b.date));

    const today = new Date().toISOString().slice(0, 10);

    return `<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>CodeWhy Timeline</title>
<style>
  *, *::before, *::after { box-sizing: border-box; }

  body {
    font-family: var(--vscode-font-family);
    font-size: var(--vscode-font-size);
    color: var(--vscode-editor-foreground);
    background: var(--vscode-editor-background);
    padding: 32px 40px 48px;
    max-width: 780px;
    margin: 0 auto;
    line-height: 1.6;
  }

  /* ── 헤더 ── */
  .brand {
    font-size: 0.68em;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    color: var(--vscode-descriptionForeground);
    margin-bottom: 6px;
  }
  .file-name {
    font-size: 1.25em;
    font-weight: 700;
    margin: 0 0 4px;
    word-break: break-all;
  }
  .file-path {
    font-size: 0.75em;
    color: var(--vscode-descriptionForeground);
    margin-bottom: 28px;
    word-break: break-all;
  }

  /* ── 요약 카드 ── */
  .summary-label {
    font-size: 0.68em;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    color: var(--vscode-descriptionForeground);
    margin-bottom: 8px;
  }
  .summary-card {
    background: var(--vscode-textBlockQuote-background);
    border-left: 3px solid var(--vscode-textLink-activeForeground);
    border-radius: 0 6px 6px 0;
    padding: 14px 18px;
    margin-bottom: 36px;
    font-size: 0.95em;
    line-height: 1.75;
  }

  /* ── 마일스톤 헤더 ── */
  .section-row {
    display: flex;
    align-items: center;
    gap: 10px;
    margin-bottom: 24px;
  }
  .section-title {
    font-size: 0.68em;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    color: var(--vscode-descriptionForeground);
    white-space: nowrap;
  }
  .section-count {
    font-size: 0.68em;
    padding: 1px 7px;
    border-radius: 8px;
    background: var(--vscode-badge-background);
    color: var(--vscode-badge-foreground);
  }
  .divider {
    flex: 1;
    height: 1px;
    background: var(--vscode-widget-border);
  }

  /* ── 타임라인 ── */
  .timeline {
    position: relative;
    padding-left: 36px;
  }
  /* 세로 선 */
  .timeline::before {
    content: '';
    position: absolute;
    left: 9px;
    top: 10px;
    bottom: 10px;
    width: 2px;
    background: var(--vscode-widget-border);
    border-radius: 1px;
  }

  .milestone {
    position: relative;
    margin-bottom: 28px;
  }
  .milestone:last-child {
    margin-bottom: 0;
  }

  /* 타임라인 점 */
  .dot {
    position: absolute;
    left: -30px;
    top: 5px;
    width: 10px;
    height: 10px;
    border-radius: 50%;
    background: var(--vscode-textLink-activeForeground);
    border: 2px solid var(--vscode-editor-background);
    outline: 2px solid var(--vscode-textLink-activeForeground);
  }
  /* 첫 번째·마지막 점 강조 */
  .milestone:first-child .dot,
  .milestone:last-child .dot {
    width: 12px;
    height: 12px;
    left: -31px;
    top: 4px;
  }

  .date-chip {
    display: inline-block;
    font-size: 0.72em;
    padding: 2px 9px;
    border-radius: 10px;
    background: var(--vscode-badge-background);
    color: var(--vscode-badge-foreground);
    margin-bottom: 6px;
    letter-spacing: 0.02em;
    font-variant-numeric: tabular-nums;
  }
  .desc {
    font-size: 0.92em;
    line-height: 1.65;
    color: var(--vscode-editor-foreground);
  }

  /* ── 푸터 ── */
  .footer {
    margin-top: 44px;
    padding-top: 14px;
    border-top: 1px solid var(--vscode-widget-border);
    display: flex;
    justify-content: space-between;
    font-size: 0.7em;
    color: var(--vscode-descriptionForeground);
  }
</style>
</head>
<body>

  <div class="brand">CodeWhy · Timeline Summary</div>
  <h1 class="file-name">${e(fileName)}</h1>
  <div class="file-path">${e(fullPath)}</div>

  <div class="summary-label">AI 요약</div>
  <div class="summary-card">${e(result.summary)}</div>

  <div class="section-row">
    <span class="section-title">주요 마일스톤</span>
    <span class="section-count">${sorted.length}건</span>
    <div class="divider"></div>
  </div>

  <div class="timeline">
    ${sorted.length > 0 ? sorted.map(buildMilestone).join('') : '<div class="desc">마일스톤이 없습니다.</div>'}
  </div>

  <div class="footer">
    <span>Generated by CodeWhy AI</span>
    <span>${today}</span>
  </div>

</body>
</html>`;
}

function buildMilestone(m: TimelineMilestone): string {
    return `<div class="milestone">
  <div class="dot"></div>
  <div class="date-chip">${e(m.date)}</div>
  <div class="desc">${e(m.description)}</div>
</div>`;
}

/** XSS 방지용 HTML 이스케이프 */
function e(text: string): string {
    return text
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}
