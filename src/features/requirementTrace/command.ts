import { execSync } from 'child_process';
import * as path from 'path';
import * as vscode from 'vscode';
import { getEditorContext } from '../../shared/editor';
import { getBackendUrl } from '../../shared/http';

/**
 * `codewhy.requirementTrace` 명령 핸들러.
 *
 * 👤 담당: 개발자 C
 */
export async function runRequirementTrace(_context: vscode.ExtensionContext) {
    const ctx = getEditorContext();
    if (!ctx) { return; }

    const backendUrl = getBackendUrl();

    let commits: CommitEntry[] = [];
    await vscode.window.withProgress(
        { location: vscode.ProgressLocation.Notification, title: 'CodeWhy: 커밋 이력 조회 중...' },
        async () => { commits = getLineCommits(ctx.repoPath, ctx.filePath, ctx.line); }
    );

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

function getLineCommits(repoPath: string, filePath: string, line: number): CommitEntry[] {
    try {
        const relativePath = path.relative(repoPath, filePath).replace(/\\/g, '/');
        const out = execSync(
            `git log -L ${line},${line}:"${relativePath}" --pretty=format:"%h|%ad|%an|%s" --date=short`,
            { cwd: repoPath, encoding: 'utf8' }
        );
        return out.trim().split('\n')
            .filter((l: string) => /^[0-9a-f]{6,10}\|/.test(l))
            .map((l: string) => {
                const [hash, date, author, ...rest] = l.split('|');
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
    const PAGE_SIZE = 10;

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
  td { padding:8px 10px; border-bottom:1px solid var(--vscode-panel-border); vertical-align:top; word-break:break-all; }
  tr:hover td { background:var(--vscode-list-hoverBackground); }
  code { font-family:monospace; font-size:11px; color:#a78bfa; }

  /* 페이징 */
  .paging { display:flex; align-items:center; justify-content:center; gap:8px; margin-top:16px; }
  .paging button { background:var(--vscode-button-secondaryBackground,#3a3a3a); color:var(--vscode-button-secondaryForeground,#ccc); border:none; border-radius:4px; padding:5px 14px; font-size:12px; cursor:pointer; }
  .paging button:disabled { opacity:0.35; cursor:default; }
  .paging button:not(:disabled):hover { background:var(--vscode-button-secondaryHoverBackground,#4a4a4a); }
  .paging .page-info { font-size:12px; color:#888; min-width:80px; text-align:center; }

  .btn-wrap { margin-top:24px; text-align:center; }
  button.primary { background:#2563eb; color:#fff; border:none; border-radius:6px; padding:10px 32px; font-size:14px; font-weight:600; cursor:pointer; }
  button.primary:hover { background:#1d4ed8; }

  /* 모달 */
  .overlay { display:none; position:fixed; inset:0; background:rgba(0,0,0,0.5); z-index:100; }
  .overlay.open { display:flex; align-items:center; justify-content:center; }
  .modal { background:var(--vscode-editor-background); border:1px solid var(--vscode-panel-border); border-radius:8px; width:500px; max-width:90vw; max-height:70vh; display:flex; flex-direction:column; }
  .modal-header { padding:16px 20px; border-bottom:1px solid var(--vscode-panel-border); display:flex; justify-content:space-between; align-items:center; }
  .modal-header h3 { margin:0; font-size:14px; }
  .modal-close { background:none; border:none; color:var(--vscode-foreground); font-size:18px; cursor:pointer; }
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
<h2>📄 ${escape(fileName)} — L${ctx.line} 커밋 이력</h2>
<div class="subtitle" id="totalInfo">불러오는 중...</div>

<table>
  <thead>
    <tr><th>#</th><th>Hash</th><th>날짜</th><th>작성자</th><th>메시지</th></tr>
  </thead>
  <tbody id="tbody"></tbody>
</table>

<div class="paging">
  <button id="prevBtn" disabled>◀ 이전</button>
  <span class="page-info" id="pageInfo"></span>
  <button id="nextBtn">다음 ▶</button>
</div>

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
    <div class="modal-body" id="modalBody"></div>
  </div>
</div>

<script>
  const BACKEND = '${backendUrl}';
  const PAGE_SIZE = ${PAGE_SIZE};
  const commits = ${commitsJson};
  let currentPage = 0;

  function totalPages() { return Math.max(1, Math.ceil(commits.length / PAGE_SIZE)); }

  function renderTable() {
    const start = currentPage * PAGE_SIZE;
    const slice = commits.slice(start, start + PAGE_SIZE);
    const tbody = document.getElementById('tbody');

    document.getElementById('totalInfo').textContent =
      commits.length === 0
        ? '이 줄과 연관된 커밋이 없습니다.'
        : \`총 \${commits.length}건 (페이지 \${currentPage + 1} / \${totalPages()})\`;

    if (commits.length === 0) {
      tbody.innerHTML = '<tr><td colspan="5" style="text-align:center;color:#888;padding:20px">커밋 이력이 없습니다.</td></tr>';
      document.getElementById('pageInfo').textContent = '';
      document.getElementById('prevBtn').disabled = true;
      document.getElementById('nextBtn').disabled = true;
      return;
    }

    tbody.innerHTML = slice.map((c, i) => \`
      <tr>
        <td>\${start + i + 1}</td>
        <td><code>\${c.hash}</code></td>
        <td style="color:#888">\${c.date}</td>
        <td>\${c.author}</td>
        <td>\${c.message}</td>
      </tr>\`).join('');

    document.getElementById('pageInfo').textContent = \`\${currentPage + 1} / \${totalPages()}\`;
    document.getElementById('prevBtn').disabled = currentPage === 0;
    document.getElementById('nextBtn').disabled = currentPage >= totalPages() - 1;
  }

  document.getElementById('prevBtn').addEventListener('click', () => { currentPage--; renderTable(); });
  document.getElementById('nextBtn').addEventListener('click', () => { currentPage++; renderTable(); });

  // 기획서 검색
  function extractKeywords(commits) {
    const words = new Set();
    const filePattern = /[\\w가-힣\\-_]+\\.[a-zA-Z]{2,5}/g;
    const wordPattern = /[가-힣A-Za-z0-9]{2,}/g;
    for (const c of commits) {
      (c.message.match(filePattern) || []).forEach(w => words.add(w));
      (c.message.match(wordPattern) || []).forEach(w => words.add(w));
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
        <button class="btn-dl" onclick="downloadDoc('\${BACKEND}\${doc.downloadUrl}', '\${doc.name}')">⬇ 다운로드</button>
      </div>\`).join('');
  }

  async function downloadDoc(url, name) {
    try {
      const res = await fetch(url);
      const blob = await res.blob();
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = name;
      a.click();
    } catch { alert('다운로드 실패: 백엔드 서버를 확인해주세요.'); }
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
      renderDocs(await res.json());
    } catch {
      document.getElementById('modalBody').innerHTML = '<div class="empty">백엔드 서버에 연결할 수 없습니다.</div>';
    }
  });

  document.getElementById('closeBtn').addEventListener('click', () => {
    document.getElementById('overlay').classList.remove('open');
  });
  document.getElementById('overlay').addEventListener('click', (e) => {
    if (e.target === document.getElementById('overlay'))
      document.getElementById('overlay').classList.remove('open');
  });

  renderTable();
</script>
</body>
</html>`;
}
