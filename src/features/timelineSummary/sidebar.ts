import * as vscode from 'vscode';
import { EditorContext } from '../../shared/editor';
import { TimelineResult } from '../../shared/types';

/**
 * Timeline Summary 사이드바.
 *
 * 구성 (위에서 아래):
 *   ┌ 헤더 (📅 TIMELINE SUMMARY + 새로고침 아이콘)
 *   ├ 파일 브레드크럼
 *   ├ AI 요약 카드
 *   ├ 주요 마일스톤 타임라인
 *   └ 푸터
 *
 * 라이프사이클:
 *   - WebviewView 는 활성화될 때 한 번 resolve 된다.
 *   - 분석 결과마다 setTimeline() → postMessage 로 DOM 만 갱신한다.
 *
 * 👤 담당: 개발자 B
 */

export const VIEW_ID = 'codewhy.timelineSummary';

export class TimelineSidebarProvider implements vscode.WebviewViewProvider {
    private view?: vscode.WebviewView;
    private last?: { ctx: EditorContext; result: TimelineResult };

    constructor(private readonly extensionUri: vscode.Uri) {}

    resolveWebviewView(view: vscode.WebviewView) {
        this.view = view;
        view.webview.options = { enableScripts: true, localResourceRoots: [this.extensionUri] };
        view.webview.html = this._buildShell();

        // 사이드바가 다시 열렸을 때 마지막 결과 복원
        if (this.last) {
            this._postRender(this.last.ctx, this.last.result);
        } else {
            this._postEmpty();
        }
    }

    /** Timeline Summary 분석이 끝났을 때 command.ts 에서 호출. */
    setTimeline(ctx: EditorContext, result: TimelineResult) {
        this.last = { ctx, result };
        if (!this.view) {
            vscode.commands.executeCommand(`${VIEW_ID}.focus`);
            return;
        }
        this._postRender(ctx, result);
        this.view.show?.(true);
    }

    /** 분석 중 스피너 표시. */
    showLoading(ctx: EditorContext) {
        if (!this.view) {
            vscode.commands.executeCommand(`${VIEW_ID}.focus`);
            return;
        }
        const fileName = ctx.filePath.split(/[\\/]/).pop() ?? ctx.filePath;
        this.view.webview.postMessage({ type: 'loading', payload: { fileName } });
        this.view.show?.(true);
    }

    // ── postMessage 헬퍼 ──────────────────────────────────────────────────

    private _postEmpty() {
        this.view?.webview.postMessage({ type: 'empty' });
    }

    private _postRender(ctx: EditorContext, result: TimelineResult) {
        const fileName = ctx.filePath.split(/[\\/]/).pop() ?? ctx.filePath;
        const sorted = [...result.milestones].sort((a, b) => a.date.localeCompare(b.date));
        this.view?.webview.postMessage({
            type: 'render',
            payload: {
                fileName,
                filePath: ctx.filePath,
                summary: result.summary,
                milestones: sorted,
            },
        });
    }

    // ── HTML 셸 (한 번만 렌더, 이후 JS 로 DOM 갱신) ──────────────────────

    private _buildShell(): string {
        return `<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline';">
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

  body {
    font-family: var(--vscode-font-family);
    font-size: var(--vscode-font-size);
    color: var(--vscode-editor-foreground);
    background: var(--vscode-sideBar-background, var(--vscode-editor-background));
    padding: 0;
    height: 100vh;
    overflow: hidden;
  }

  /* ── 헤더 ── */
  .header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 10px 14px 8px;
    border-bottom: 1px solid var(--vscode-widget-border);
    background: var(--vscode-sideBarSectionHeader-background);
    position: sticky;
    top: 0;
    z-index: 10;
  }
  .header-title {
    font-size: 0.68em;
    font-weight: 700;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    color: var(--vscode-sideBarTitle-foreground, var(--vscode-foreground));
  }

  /* ── 스크롤 영역 ── */
  .scroll {
    height: calc(100vh - 40px);
    overflow-y: auto;
    padding: 14px;
  }

  /* ── 빈 상태 ── */
  .empty {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 10px;
    height: 200px;
    color: var(--vscode-descriptionForeground);
    font-size: 0.85em;
    text-align: center;
    padding: 0 16px;
  }
  .empty-icon { font-size: 2em; opacity: 0.4; }

  /* ── 로딩 ── */
  .loading {
    display: flex; align-items: center; gap: 8px;
    color: var(--vscode-descriptionForeground);
    font-size: 0.82em;
    padding: 16px 0;
  }
  .spinner {
    width: 14px; height: 14px;
    border: 2px solid var(--vscode-widget-border);
    border-top-color: var(--vscode-progressBar-background);
    border-radius: 50%;
    animation: spin 0.8s linear infinite;
  }
  @keyframes spin { to { transform: rotate(360deg); } }

  /* ── 파일 경로 ── */
  .file-path {
    font-size: 0.78em;
    color: var(--vscode-descriptionForeground);
    word-break: break-all;
    margin-bottom: 12px;
    padding-bottom: 10px;
    border-bottom: 1px solid var(--vscode-widget-border);
  }
  .file-name { font-weight: 600; color: var(--vscode-editor-foreground); }

  /* ── 섹션 레이블 ── */
  .section-label {
    font-size: 0.65em;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    color: var(--vscode-descriptionForeground);
    margin-bottom: 8px;
  }

  /* ── 요약 카드 ── */
  .summary-card {
    background: var(--vscode-textBlockQuote-background);
    border-left: 3px solid var(--vscode-textLink-activeForeground);
    border-radius: 0 5px 5px 0;
    padding: 10px 13px;
    font-size: 0.88em;
    line-height: 1.7;
    margin-bottom: 18px;
    word-break: break-word;
  }

  /* ── 마일스톤 타임라인 ── */
  .timeline {
    position: relative;
    padding-left: 28px;
    margin-bottom: 16px;
  }
  .timeline::before {
    content: '';
    position: absolute;
    left: 7px; top: 8px; bottom: 8px;
    width: 2px;
    background: var(--vscode-widget-border);
    border-radius: 1px;
  }
  .milestone {
    position: relative;
    margin-bottom: 18px;
  }
  .milestone:last-child { margin-bottom: 0; }
  .dot {
    position: absolute;
    left: -23px; top: 5px;
    width: 8px; height: 8px;
    border-radius: 50%;
    background: var(--vscode-textLink-activeForeground);
    border: 2px solid var(--vscode-sideBar-background, var(--vscode-editor-background));
    outline: 2px solid var(--vscode-textLink-activeForeground);
  }
  .milestone:first-child .dot,
  .milestone:last-child .dot { width: 10px; height: 10px; left: -24px; top: 4px; }
  .date-chip {
    display: inline-block;
    font-size: 0.68em;
    padding: 1px 7px;
    border-radius: 8px;
    background: var(--vscode-badge-background);
    color: var(--vscode-badge-foreground);
    margin-bottom: 4px;
    font-variant-numeric: tabular-nums;
  }
  .milestone-desc {
    font-size: 0.84em;
    line-height: 1.55;
    color: var(--vscode-editor-foreground);
    word-break: break-word;
  }

  /* ── 푸터 ── */
  .footer {
    margin-top: 14px;
    padding-top: 10px;
    border-top: 1px solid var(--vscode-widget-border);
    font-size: 0.68em;
    color: var(--vscode-descriptionForeground);
    text-align: center;
  }

  .hidden { display: none !important; }
</style>
</head>
<body>

<div class="header">
  <span class="header-title">📅 Timeline Summary</span>
</div>

<div class="scroll">

  <!-- 빈 상태 -->
  <div id="empty" class="empty">
    <div class="empty-icon">📅</div>
    <div>파일을 우클릭하고<br><strong>이 파일의 역사 요약</strong>을 선택하세요.</div>
  </div>

  <!-- 로딩 -->
  <div id="loading" class="loading hidden">
    <div class="spinner"></div>
    <span id="loading-file">분석 중...</span>
  </div>

  <!-- 결과 -->
  <div id="result" class="hidden">
    <div class="file-path">
      <span class="file-name" id="file-name"></span><br>
      <span id="file-path-full"></span>
    </div>

    <div class="section-label">AI 요약</div>
    <div class="summary-card" id="summary-text"></div>

    <div class="section-label" id="milestone-label">주요 마일스톤</div>
    <div class="timeline" id="timeline"></div>

    <div class="footer">Generated by CodeWhy AI</div>
  </div>

</div>

<script>
  const vscode = acquireVsCodeApi();

  function e(text) {
    return String(text ?? '')
      .replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  function show(id) {
    ['empty','loading','result'].forEach(i => {
      document.getElementById(i).classList.toggle('hidden', i !== id);
    });
  }

  window.addEventListener('message', ({ data: msg }) => {
    switch (msg.type) {

      case 'empty':
        show('empty');
        break;

      case 'loading':
        document.getElementById('loading-file').textContent =
          (msg.payload?.fileName ?? '') + ' 분석 중...';
        show('loading');
        break;

      case 'render': {
        const { fileName, filePath, summary, milestones } = msg.payload;

        document.getElementById('file-name').textContent = fileName;
        document.getElementById('file-path-full').textContent = filePath;
        document.getElementById('summary-text').textContent = summary;

        const tl = document.getElementById('timeline');
        const lbl = document.getElementById('milestone-label');

        if (!milestones || milestones.length === 0) {
          lbl.classList.add('hidden');
          tl.classList.add('hidden');
        } else {
          lbl.classList.remove('hidden');
          tl.classList.remove('hidden');
          tl.innerHTML = milestones.map(m => \`
            <div class="milestone">
              <div class="dot"></div>
              <div class="date-chip">\${e(m.date)}</div>
              <div class="milestone-desc">\${e(m.description)}</div>
            </div>
          \`).join('');
        }

        show('result');
        break;
      }
    }
  });
</script>
</body>
</html>`;
    }
}
