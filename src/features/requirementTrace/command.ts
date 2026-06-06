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
    const backendUrl = vscode.workspace
        .getConfiguration('codewhy')
        .get<string>('backendUrl', 'http://localhost:8000');

    const panel = vscode.window.createWebviewPanel(
        'requirementTrace',
        'CodeWhy: 원본 기획서 찾기',
        vscode.ViewColumn.Beside,
        { enableScripts: true }
    );
    panel.webview.html = buildHtml(ctx, commits, backendUrl);
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

function buildHtml(
    ctx: { filePath: string; line: number },
    commits: CommitEntry[],
    backendUrl: string
): string {
    const fileName = ctx.filePath.split(/[\\/]/).pop() ?? ctx.filePath;
    const commitsJson = JSON.stringify(commits);

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
  button.primary { background:#2563eb; color:#fff; border:none; border-radius:6px; padding:10px 32px; font-size:14px; font-weight:600; cursor:pointer; }
  button.primary:hover { background:#1d4ed8; }

  /* 모달 */
  .overlay { display:none; position:fixed; inset:0; background:rgba(0,0,0,0.5); z-index:100; }
  .overlay.open { display:flex; align-items:center; justify-content:center; }
  .modal { background:var(--vscode-editor-background); border:1px solid var(--vscode-panel-border); border-radius:8px; width:500px; max-width:90vw; max-height:70vh; display:flex; flex-direction:column; }
  .modal-header { padding:16px 20px; border-bottom:1px solid var(--vscode-panel-border); display:flex; justify-content:space-between; align-items:center; }
  .modal-header h3 { margin:0; font-size:14px; }
  .modal-close { background:none; border:none; color:var(--vscode-foreground); font-size:18px; cursor:pointer; padding:0 4px; }
  .modal-body { padding:12px 20px; overflow-y:auto; flex:1; }
  .doc-item { display:flex; align-items:center; justify-content:space-between; padding:10px 0; border-bottom:1px solid var(--vscode-panel-border); }
  .doc-item:last-child { border-bottom:none; }
  .doc-name { font-size:13px; }
  .doc-meta { font-size:11px; color:#888; margin-top:2px; }
  .btn-dl { background:#2563eb; color:#fff; border:none; border-radius:4px; padding:5px 14px; font-size:12px; cursor:pointer; white-space:nowrap; }
  .btn-dl:hover { background:#1d4ed8; }
  .empty { text-align:center; color:#888; padding:30px 0; }
  .loading { text-align:center; color:#888; padding:30px 0; }
</style>
</head>
<body>
<h2>📄 ${escape(fileName)} — 커밋 이력</h2>
<div class="subtitle">L${ctx.line} 기준 최근 커밋 최대 10건</div>

<table>
  <thead>
    <tr><th>#</th><th>Hash</th><th>날짜</th><th>작성자</th><th>메시지</th></tr>
  </thead>
  <tbody>${rows}</tbody>
</table>

<div class="btn-wrap">
  <button class="primary" id="findBtn">🔍 원본 기획서 찾기</button>
</div>

<!-- 모달 -->
<div class="overlay" id="overlay">
  <div class="modal">
    <div class="modal-header">
      <h3>📁 연관 기획서 목록</h3>
      <button class="modal-close" id="closeBtn">✕</button>
    </div>
    <div class="modal-body" id="modalBody">
      <div class="loading">검색 중...</div>
    </div>
  </div>
</div>

<script>
  const BACKEND = '${backendUrl}';
  const commits = ${commitsJson};

  function extractKeywords(commits) {
    const words = new Set();
    const filePattern = /[\\w가-힣\\-_]+\\.[a-zA-Z]{2,5}/g;
    const wordPattern = /[가-힣A-Za-z0-9]{2,}/g;
    for (const c of commits) {
      const fileMatches = c.message.match(filePattern) || [];
      fileMatches.forEach(w => words.add(w));
      const wordMatches = c.message.match(wordPattern) || [];
      wordMatches.forEach(w => words.add(w));
    }
    return Array.from(words);
  }

  function renderDocs(docs) {
    const body = document.getElementById('modalBody');
    if (!docs || docs.length === 0) {
      body.innerHTML = '<div class="empty">연관 기획서를 찾지 못했습니다.</div>';
      return;
    }
    body.innerHTML = docs.map(doc => \`
      <div class="doc-item">
        <div>
          <div class="doc-name">📄 \${doc.name}</div>
          \${doc.pageCount ? \`<div class="doc-meta">\${doc.pageCount}페이지</div>\` : ''}
        </div>
        <button class="btn-dl" onclick="downloadDoc('\${BACKEND}\${doc.downloadUrl}', '\${doc.name}')">
          ⬇ 다운로드
        </button>
      </div>
    \`).join('');
  }

  async function downloadDoc(url, name) {
    try {
      const res = await fetch(url);
      const blob = await res.blob();
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = name;
      a.click();
    } catch {
      alert('다운로드 실패: 백엔드 서버를 확인해주세요.');
    }
  }

  document.getElementById('findBtn').addEventListener('click', async () => {
    document.getElementById('overlay').classList.add('open');
    document.getElementById('modalBody').innerHTML = '<div class="loading">검색 중...</div>';

    const keywords = extractKeywords(commits);
    try {
      const res = await fetch(BACKEND + '/api/documents/search', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ keywords })
      });
      const docs = await res.json();
      renderDocs(docs);
    } catch {
      document.getElementById('modalBody').innerHTML =
        '<div class="empty">백엔드 서버에 연결할 수 없습니다.</div>';
    }
  });

  document.getElementById('closeBtn').addEventListener('click', () => {
    document.getElementById('overlay').classList.remove('open');
  });

  document.getElementById('overlay').addEventListener('click', (e) => {
    if (e.target === document.getElementById('overlay')) {
      document.getElementById('overlay').classList.remove('open');
    }
  });
</script>
</body>
</html>`;
}
