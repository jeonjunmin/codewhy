import { execFileSync } from 'child_process';
import * as path from 'path';
import { BlameUnavailableReason, CommitInput, GitCommitMeta } from './types';

/**
 * 로컬 git 헬퍼 — 백엔드 app/core/git.py 의 blame 계열 로직을 확장(클라이언트)으로 이식한 것.
 *
 * 배경: 백엔드를 원격(AWS)에 올리면 사용자 로컬 저장소(c:\Source\...)에 접근할 수 없어
 * 서버에서 git 을 돌릴 수 없다. 그래서 git 실행은 저장소가 있는 이곳(확장)에서 하고,
 * 그 결과(blame 메타/diff/라인 이력/후속 커밋/remote)를 백엔드로 보낸다.
 * 백엔드는 받은 데이터로 LLM 추론·DB 캐시·GitHub/GitLab API 조회만 담당한다.
 *
 * 모든 함수는 '정상적인 실패'(미커밋 라인, remote 없음 등)를 예외가 아니라
 * null/빈 값으로 돌려준다 — 호출부가 그대로 진행하도록.
 */

/** blame/commit 해석 결과 — 성공이면 meta, 정상 실패면 unavailable 사유. */
export interface GitCommitResult {
    meta: GitCommitMeta | null;
    unavailable: BlameUnavailableReason | null;
}

// ─── 저수준 실행 ──────────────────────────────────────────────────────────────

interface GitRun { ok: boolean; stdout: string; stderr: string; }

/** git 을 cwd=repoPath 에서 실행한다. 셸을 거치지 않아 경로 따옴표 이슈가 없다. */
function runGit(repoPath: string, args: string[]): GitRun {
    try {
        const stdout = execFileSync('git', args, {
            cwd: repoPath,
            encoding: 'utf-8',
            timeout: 10_000,
            // stderr 는 따로 받기 위해 파이프. 실패 시 throw 되며 e.stderr 로 접근.
            stdio: ['ignore', 'pipe', 'pipe'],
            maxBuffer: 16 * 1024 * 1024,
        });
        return { ok: true, stdout, stderr: '' };
    } catch (e: unknown) {
        const err = e as { stdout?: Buffer | string; stderr?: Buffer | string };
        return {
            ok: false,
            stdout: err.stdout ? err.stdout.toString() : '',
            stderr: err.stderr ? err.stderr.toString() : '',
        };
    }
}

/** git 인자로 넘길 저장소 상대경로(슬래시 정규화). 절대경로보다 호환성이 높다. */
function relPath(repoPath: string, filePath: string): string {
    const rel = path.relative(repoPath, filePath);
    return rel.split(path.sep).join('/');
}

function parsePipeCommits(out: string): CommitInput[] {
    const commits: CommitInput[] = [];
    for (const raw of out.trim().split('\n')) {
        if (!raw) { continue; }
        const parts = raw.split('|');
        if (parts.length < 4) { continue; }
        const [hash, author, date, ...rest] = parts;
        commits.push({ hash, author, date, subject: rest.join('|') });
    }
    return commits;
}

// ─── blame / commit 메타 ──────────────────────────────────────────────────────

function isTracked(repoPath: string, file: string): boolean {
    return runGit(repoPath, ['ls-files', '--error-unmatch', '--', file]).ok;
}

function commitMessage(repoPath: string, hash: string): string {
    return runGit(repoPath, ['log', '-1', '--format=%B', hash]).stdout.trim();
}

/**
 * 커밋이 해당 파일에 가한 diff(stat+patch).
 * merge 커밋도 일반 diff 를 얻도록 `-m --first-parent` 강제(git.py 와 동일).
 */
function commitDiff(repoPath: string, hash: string, file: string): string {
    return runGit(repoPath, [
        'show', '-p', '--stat', '-m', '--first-parent', hash, '--', file,
    ]).stdout.trim();
}

/**
 * 이 커밋이 해당 파일에 더하고 지운 라인 수.
 * blame 은 rename 을 따라가므로 `git show <hash> -- <현재경로>` 가 비기 쉽다.
 * rename 추적되는 `git log --follow --numstat` 으로 그 커밋의 행을 찾는다(git.py 와 동일).
 */
function commitNumstat(repoPath: string, hash: string, file: string): { added: number; removed: number } {
    const out = runGit(repoPath, [
        'log', '--follow', '--numstat', '--format=__%H', '--', file,
    ]).stdout;
    let inTarget = false;
    for (const line of out.split('\n')) {
        if (line.startsWith('__')) {
            const h = line.slice(2);
            inTarget = !!h && (h.startsWith(hash) || hash.startsWith(h));
            continue;
        }
        if (inTarget) {
            const cols = line.split('\t');
            if (cols.length >= 2) {
                const added = /^\d+$/.test(cols[0]) ? Number(cols[0]) : 0;
                const removed = /^\d+$/.test(cols[1]) ? Number(cols[1]) : 0;
                return { added, removed };
            }
        }
    }
    return { added: 0, removed: 0 };
}

/** author-time(유닉스 초)을 UTC 기준 YYYY-MM-DD 로(백엔드 strftime 과 일치). */
function formatUnixDate(raw: string): string {
    const ts = Number(raw);
    if (!Number.isFinite(ts)) { return raw; }
    return new Date(ts * 1000).toISOString().slice(0, 10);
}

/**
 * 특정 라인의 마지막 커밋 메타 + diff 를 조립한다. (git.py:get_blame_info)
 * 미커밋/이력 없음은 예외가 아니라 unavailable 사유로 돌려준다.
 */
export function getBlameInfo(repoPath: string, filePath: string, line: number): GitCommitResult {
    const file = relPath(repoPath, filePath);
    const blame = runGit(repoPath, ['blame', '-L', `${line},${line}`, '--porcelain', '--', file]);
    if (!blame.ok) {
        const reason: BlameUnavailableReason = isTracked(repoPath, file) ? 'no_history' : 'uncommitted';
        return { meta: null, unavailable: reason };
    }

    const lines = blame.stdout.split('\n');
    const commitHash = (lines[0] ?? '').split(' ')[0] ?? '';
    const author = (lines.find(l => l.startsWith('author ')) ?? '').replace(/^author /, '');
    const rawTs = (lines.find(l => l.startsWith('author-time ')) ?? '').replace(/^author-time /, '');

    return {
        meta: {
            commitHash,
            author,
            date: rawTs ? formatUnixDate(rawTs) : '',
            message: commitMessage(repoPath, commitHash),
            diff: commitDiff(repoPath, commitHash, file),
            ...commitNumstat(repoPath, commitHash, file),
        },
        unavailable: null,
    };
}

/**
 * 임의 커밋 해시 하나의 메타 + 해당 파일 diff 를 조립한다. (git.py:get_commit_info)
 * '라인 수정 이력' 항목 펼침(/reason)에서 쓴다. 해시가 유효하지 않으면 no_history.
 */
export function getCommitInfo(repoPath: string, filePath: string, hash: string): GitCommitResult {
    const file = relPath(repoPath, filePath);
    const meta = runGit(repoPath, ['show', '-s', '--format=%H|%an|%ad', '--date=short', hash]);
    if (!meta.ok) {
        return { meta: null, unavailable: 'no_history' };
    }
    const parts = meta.stdout.trim().split('|');
    const commitHash = parts[0] || hash;
    return {
        meta: {
            commitHash,
            author: parts[1] ?? '',
            date: parts[2] ?? '',
            message: commitMessage(repoPath, hash),
            diff: commitDiff(repoPath, hash, file),
            ...commitNumstat(repoPath, hash, file),
        },
        unavailable: null,
    };
}

// ─── 라인 이력 / 후속 커밋 / 브랜치 / remote ──────────────────────────────────

/**
 * 특정 라인이 '실제로 바뀐' 커밋 이력(최신순, 최대 maxCount). (git.py:get_line_history)
 * `git log -L<line>,<line>:<file>` 로 그 한 줄의 변천만 추린다. 실패 시 빈 배열.
 */
export function getLineHistory(
    repoPath: string, filePath: string, line: number, maxCount = 8,
): CommitInput[] {
    const file = relPath(repoPath, filePath);
    const out = runGit(repoPath, [
        'log', '-s', `-L${line},${line}:${file}`, `-n${maxCount}`,
        '--format=%H|%an|%ad|%s', '--date=short',
    ]);
    return out.ok ? parsePipeCommits(out.stdout) : [];
}

/**
 * 같은 티켓(예: PAY-2041)을 참조하는 다른 커밋들. (git.py:find_followup_commits)
 * 블레임 대상 커밋 자신(excludeHash)은 제외. 티켓이 없으면 빈 배열.
 */
export function getFollowupCommits(
    repoPath: string, ticket: string | null, excludeHash = '',
): CommitInput[] {
    if (!ticket) { return []; }
    const out = runGit(repoPath, [
        'log', `--grep=${ticket}`, '--format=%H|%an|%ad|%s', '--date=short',
    ]);
    if (!out.ok) { return []; }
    return parsePipeCommits(out.stdout).filter(
        c => !(excludeHash && c.hash.startsWith(excludeHash)),
    );
}

/** 현재 체크아웃된 브랜치명. detached HEAD 등 실패 시 빈 문자열. (git.py:get_current_branch) */
export function getCurrentBranch(repoPath: string): string {
    const out = runGit(repoPath, ['rev-parse', '--abbrev-ref', 'HEAD']);
    return out.ok ? out.stdout.trim() : '';
}

/** origin remote URL 원문. 백엔드가 _parse_remote_url 로 host/owner/repo 를 뽑는다. */
export function getRemoteUrl(repoPath: string): string | null {
    const out = runGit(repoPath, ['remote', 'get-url', 'origin']);
    const url = out.ok ? out.stdout.trim() : '';
    return url || null;
}

// 이슈 트래커 키 패턴 — 백엔드 tickets.py 의 _TICKET_RE 와 동일.
const TICKET_RE = /\b([A-Z][A-Z0-9]+-\d+)\b/;

/** 커밋 메시지·브랜치명에서 티켓 키 추출(메시지 우선). (tickets.py:extract_ticket) */
export function extractTicket(message: string, branch = ''): string | null {
    for (const text of [message, branch]) {
        const m = TICKET_RE.exec(text || '');
        if (m) { return m[1]; }
    }
    return null;
}
