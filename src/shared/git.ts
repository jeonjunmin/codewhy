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

// ─── 변경 신호(diff) 파싱 / 라인 단위 신호 ─────────────────────────────────────

/**
 * 커밋별 '실제 코드 변경' 신호.
 *   · added/removed : +/- 줄 수
 *   · symbols       : 바뀐 함수/섹션 이름(쉼표 구분, best-effort — -L 단일 라인엔 보통 없음)
 *   · changedLines  : 바뀐 라인의 실제 텍스트(`old → new`, 압축·절단). 카운트/함수명이 못 담는
 *                     '무엇이 바뀌었나'를 담아, 모호한 메시지를 코드 기준으로 교정하게 한다.
 */
export interface FileChangeSignal { added: number; removed: number; symbols: string; changedLines: string; }

// changedLines 토큰·payload 상한 — 거대 헌크가 프롬프트를 폭발시키지 않도록.
const CHANGED_LINES_MAX = 200;

/** 바뀐 라인들(제거/추가 텍스트)을 한 줄 `old → new` 신호로 압축한다. 없으면 빈 문자열. */
function formatChangedLines(removed: string[], added: string[]): string {
    const clip = (s: string) => s.length > CHANGED_LINES_MAX ? s.slice(0, CHANGED_LINES_MAX) + '…' : s;
    const r = removed.join(' ⏎ ').trim();
    const a = added.join(' ⏎ ').trim();
    if (r && a) { return clip(`${r} → ${a}`); }
    if (a) { return clip(`+ ${a}`); }
    if (r) { return clip(`- ${r}`); }
    return '';
}

/**
 * `git log … -p --format=%x01%H` 출력을 커밋별 변경 신호로 파싱한다.
 * %x01(0x01) 은 패치 본문과 안 겹치는 레코드 경계. 커밋마다 +/- 줄 수, 함수 컨텍스트,
 * 그리고 바뀐 라인 텍스트만 추리고 패치 본문은 버린다(토큰·payload 최소화).
 * 파일 전체 diff(타임라인)와 라인 단위 diff(-L, 라인 타이틀) 양쪽에서 공유한다.
 */
export function parseChangeSignals(out: string): Map<string, FileChangeSignal> {
    const map = new Map<string, FileChangeSignal>();
    for (const block of out.split('\x01')) {
        const nl = block.indexOf('\n');
        const hash = (nl < 0 ? block : block.slice(0, nl)).trim();
        if (!hash) { continue; }
        const body = nl < 0 ? '' : block.slice(nl + 1);
        let added = 0, removed = 0;
        const symbols = new Set<string>();
        const removedText: string[] = [], addedText: string[] = [];
        for (const line of body.split('\n')) {
            if (line.startsWith('@@')) {
                // "@@ -a,b +c,d @@ <context>" — <context> 는 보통 이 헌크가 속한 함수/섹션명.
                const ctx = line.slice(line.indexOf('@@', 2) + 2).trim();
                if (ctx) { symbols.add(ctx); }
            } else if (line.startsWith('+') && !line.startsWith('+++')) {
                added++;
                const t = line.slice(1).trim();
                if (t) { addedText.push(t); }
            } else if (line.startsWith('-') && !line.startsWith('---')) {
                removed++;
                const t = line.slice(1).trim();
                if (t) { removedText.push(t); }
            }
        }
        // 심볼은 토큰을 아끼려고 최대 4개까지만.
        map.set(hash, {
            added, removed,
            symbols: [...symbols].slice(0, 4).join(', '),
            changedLines: formatChangedLines(removedText, addedText),
        });
    }
    return map;
}

/**
 * 한 라인이 '실제로 바뀐' 커밋들의, 그 라인 범위에 한정한 변경 신호. (라인 수정 이력 타이틀 grounding)
 * getLineHistory 와 같은 `git log -L<line>,<line>:<file>` 를 쓰되 패치를 살려(-s 없이)
 * 그 라인 헌크의 +/-줄·함수 컨텍스트만 추린다. 파일 전체가 아니라 '그 라인'의 변경이라
 * 모호한 커밋 메시지를 라인 기준으로 교정하는 데 더 정확하다. 실패 시 빈 Map.
 */
export function getLineChangeSignals(
    repoPath: string, filePath: string, line: number, maxCount = 8,
): Map<string, FileChangeSignal> {
    const file = relPath(repoPath, filePath);
    const out = runGit(repoPath, [
        'log', `-L${line},${line}:${file}`, `-n${maxCount}`,
        '--unified=0', '--format=%x01%H',
    ]);
    return out.ok ? parseChangeSignals(out.stdout) : new Map();
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
