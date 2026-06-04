import { execSync } from 'child_process';
import * as vscode from 'vscode';
import { getEditorContext } from '../../shared/editor';

/**
 * `codewhy.requirementTrace` 명령 핸들러.
 *
 * 👤 담당: 개발자 C
 */
export async function runRequirementTrace(_context: vscode.ExtensionContext) {
    const ctx = getEditorContext();
    if (!ctx) { return; }

    const commits = getRecentCommits(ctx.repoPath, ctx.filePath);

    const panel = vscode.window.createWebviewPanel(
        'requirementTrace',
        'CodeWhy: 원본 기획서 찾기',
        vscode.ViewColumn.Beside,
        { enableScripts: true }
    );
    panel.webview.html = buildHtml(ctx, commits);

    panel.webview.onDidReceiveMessage((msg) => {
        if (msg.command === 'findSpec') {
            vscode.window.showInformationMessage('CodeWhy: 원본 기획서 검색 기능은 준비 중입니다.');
        }
    });
}

interface CommitEntry {
    hash: string;
    date: string;
    author: string;
    message: string;
}

function getRecentCommits(repoPath: string, filePath: string): CommitEntry[] {
    try {
        const out = execSync(
            `git log --max-count=10 --pretty=format:"%h|%ad|%an|%s" --date=short -- "${filePath}"`,
            { cwd: repoPath, encoding: 'utf8' }
        );
        return out.trim().split('\n').filter(Boolean).map((line: string) => {
            const [hash, date, author, ...rest] = line.split('|');
            return { hash, date, author, message: rest.join('|') };
        });
    } catch {
        return [];
    }
}

function escape(s: string): string {
    return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function buildHtml(ctx: { filePath: string; line: number }, commits: CommitEntry[]): string {
    const fileName = ctx.filePath.split(/[\\/]/).pop() ?? ctx.filePath;

    const rows = commits.length === 0
        ? '<tr><td colspan="5" style="text-align:center;color:#888;padding:20px">커밋 이력이 없습니다.</td></tr>'
        : commits.map((c, i) => `
            <tr>
                <td>${i + 1}</td>
                <td><code>${escape(c.hash)}</code></td>
                <td style="color:#888">${escape(c.date)}</td>
                <td>${escape(c.author)}</td>
                <td>${escape(c.message)}</td>
            </tr>`).join('');

    return `<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<style>
  body { font-family: var(--vscode-font-family); font-size:13px; padding:20px; color:var(--vscode-foreground); background:var(--vscode-editor-background); }
  h2 { font-size:15px; margin-bottom:4px; }
  .subtitle { color:#888; font-size:12px; margin-bottom:20px; }
  table { width:100%; border-collapse:collapse; }
  th { text-align:left; padding:8px 10px; border-bottom:1px solid var(--vscode-panel-border); color:#888; font-weight:600; font-size:11px; text-transform:uppercase; }
  td { padding:8px 10px; border-bottom:1px solid var(--vscode-panel-border); vertical-align:top; }
  tr:hover td { background:var(--vscode-list-hoverBackground); }
  code { font-family:monospace; font-size:11px; color:#a78bfa; }
  .btn-wrap { margin-top:28px; text-align:center; }
  button { background:#2563eb; color:#fff; border:none; border-radius:6px; padding:10px 32px; font-size:14px; font-weight:600; cursor:pointer; letter-spacing:0.3px; }
  button:hover { background:#1d4ed8; }
</style>
</head>
<body>
<h2>📄 ${escape(fileName)} — 커밋 이력</h2>
<div class="subtitle">L${ctx.line} 기준 최근 커밋 최대 10건</div>

<table>
  <thead>
    <tr>
      <th>#</th><th>Hash</th><th>날짜</th><th>작성자</th><th>메시지</th>
    </tr>
  </thead>
  <tbody>${rows}</tbody>
</table>

<div class="btn-wrap">
  <button id="findBtn">🔍 원본 기획서 찾기</button>
</div>

<script>
  const vscode = acquireVsCodeApi();
  document.getElementById('findBtn').addEventListener('click', () => {
    vscode.postMessage({ command: 'findSpec' });
  });
</script>
</body>
</html>`;
}
