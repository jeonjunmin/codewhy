"""GitHub / GitLab 호스팅 연동.

git 로컬 정보만으로는 알 수 없는 'PR(MR) 단위' 맥락을 호스팅 API 로 가져온다:
  - prInfo            : 같은 PR 의 총 변경 라인 수 ('동일 PR 23 라인')
  - PR 변경 파일 목록 : '이 변경과 함께 일어난 일'의 같은-PR 행 재료

설계 원칙: **연동은 항상 옵셔널**. remote 미감지·토큰 미설정·API 실패 시
모두 None / 빈 값을 돌려주어, 호출 측(service.py)은 로컬 git 결과를 그대로 반환한다.

표준 라이브러리(urllib)만 사용해 의존성을 추가하지 않는다.

👤 담당: 개발자 A
"""

import json
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

from app.core.config import get_github_token, get_gitlab_token

_TIMEOUT = 6  # 초 — 호스팅 API 가 느려도 블레임 응답을 오래 잡지 않는다


@dataclass
class Remote:
    host: str    # 'github' | 'gitlab'
    owner: str   # owner 또는 group/subgroup 경로
    repo: str
    base: str    # API base URL


@dataclass
class ChangedFile:
    path: str
    added: int = 0
    status: str = ""  # 'added' | 'modified' | 'removed' (GitHub 기준)


@dataclass
class PullRequest:
    url: str
    number: int
    title: str = ""
    body: str = ""
    added: int = 0
    removed: int = 0
    files: list[ChangedFile] = field(default_factory=list)


@dataclass
class Attachment:
    """이슈 본문에 첨부된 문서/파일 한 건."""
    label: str  # 표시명 (마크다운 링크 텍스트 또는 파일명)
    url: str


@dataclass
class Issue:
    """커밋에 연결된 GitHub Issue 한 건."""
    number: int
    title: str
    url: str
    body: str = ""
    attachments: list[Attachment] = field(default_factory=list)


def detect_remote(repo_path: str) -> Remote | None:
    """`origin` remote URL 을 파싱해 호스트/소유자/저장소를 알아낸다."""
    try:
        url = subprocess.check_output(
            ["git", "remote", "get-url", "origin"],
            cwd=repo_path,
            text=True,
            encoding="utf-8",
        ).strip()
    except subprocess.CalledProcessError:
        return None
    return _parse_remote_url(url)


def find_pr_for_commit(repo_path: str, commit_hash: str) -> PullRequest | None:
    """커밋이 속한 PR/MR 을 찾아 변경 통계까지 채워 반환한다.

    어느 단계든 실패하면 None — 호출 측이 PR 정보 없이 진행한다.
    """
    remote = detect_remote(repo_path)
    if not remote or not commit_hash:
        return None
    try:
        if remote.host == "github":
            return _github_pr_for_commit(remote, commit_hash)
        if remote.host == "gitlab":
            return _gitlab_mr_for_commit(remote, commit_hash)
    except (urllib.error.URLError, KeyError, ValueError, TimeoutError):
        return None
    return None


# ─── URL 파싱 ───────────────────────────────────────────────────────
def _parse_remote_url(url: str) -> Remote | None:
    # git@github.com:owner/repo.git  또는  https://github.com/owner/repo(.git)
    m = re.match(r"(?:git@|https?://)([^/:]+)[/:](.+?)(?:\.git)?/?$", url)
    if not m:
        return None
    domain, path = m.group(1), m.group(2)
    parts = path.split("/")
    if len(parts) < 2:
        return None
    repo = parts[-1]
    owner = "/".join(parts[:-1])

    if "github" in domain:
        return Remote("github", owner, repo, f"https://api.{domain}")
    if "gitlab" in domain:
        return Remote("gitlab", owner, repo, f"https://{domain}/api/v4")
    return None


# ─── HTTP ───────────────────────────────────────────────────────────
def _get_json(url: str, headers: dict) -> object:
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


# ─── GitHub ─────────────────────────────────────────────────────────
def _github_headers() -> dict:
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "codewhy"}
    token = get_github_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _github_pr_for_commit(remote: Remote, commit_hash: str) -> PullRequest | None:
    headers = _github_headers()
    listing = _get_json(
        f"{remote.base}/repos/{remote.owner}/{remote.repo}/commits/{commit_hash}/pulls",
        headers,
    )
    if not isinstance(listing, list) or not listing:
        return None
    pr = listing[0]  # 가장 연관 높은 PR
    number = pr["number"]

    detail = _get_json(
        f"{remote.base}/repos/{remote.owner}/{remote.repo}/pulls/{number}",
        headers,
    )
    files_json = _get_json(
        f"{remote.base}/repos/{remote.owner}/{remote.repo}/pulls/{number}/files?per_page=100",
        headers,
    )
    files = (
        [
            ChangedFile(path=f["filename"], added=f.get("additions", 0), status=f.get("status", ""))
            for f in files_json
        ]
        if isinstance(files_json, list)
        else []
    )

    return PullRequest(
        url=pr.get("html_url", ""),
        number=number,
        title=detail.get("title", ""),
        body=detail.get("body") or "",
        added=detail.get("additions", 0),
        removed=detail.get("deletions", 0),
        files=files,
    )


# ─── GitLab ─────────────────────────────────────────────────────────
def _gitlab_headers() -> dict:
    headers = {"User-Agent": "codewhy"}
    token = get_gitlab_token()
    if token:
        headers["PRIVATE-TOKEN"] = token
    return headers


def _gitlab_mr_for_commit(remote: Remote, commit_hash: str) -> PullRequest | None:
    headers = _gitlab_headers()
    project_id = urllib.parse.quote(f"{remote.owner}/{remote.repo}", safe="")

    mrs = _get_json(
        f"{remote.base}/projects/{project_id}/repository/commits/{commit_hash}/merge_requests",
        headers,
    )
    if not isinstance(mrs, list) or not mrs:
        return None
    mr = mrs[0]
    iid = mr["iid"]

    changes = _get_json(
        f"{remote.base}/projects/{project_id}/merge_requests/{iid}/changes",
        headers,
    )
    diffs = changes.get("changes", []) if isinstance(changes, dict) else []
    files = [
        ChangedFile(
            path=d.get("new_path", ""),
            added=_count_added_lines(d.get("diff", "")),
            status="added" if d.get("new_file") else ("removed" if d.get("deleted_file") else "modified"),
        )
        for d in diffs
        if d.get("new_path")
    ]
    added, removed = _count_gitlab_diff_lines(diffs)

    return PullRequest(
        url=mr.get("web_url", ""),
        number=iid,
        title=mr.get("title", ""),
        body=mr.get("description") or "",
        added=added,
        removed=removed,
        files=files,
    )


def _count_added_lines(diff: str) -> int:
    return sum(1 for line in diff.splitlines() if line.startswith("+") and not line.startswith("+++"))


def _count_gitlab_diff_lines(diffs: list[dict]) -> tuple[int, int]:
    """GitLab 은 MR 통계를 안 주므로 diff 텍스트의 +/- 줄을 직접 센다."""
    added = removed = 0
    for d in diffs:
        for line in d.get("diff", "").splitlines():
            if line.startswith("+") and not line.startswith("+++"):
                added += 1
            elif line.startswith("-") and not line.startswith("---"):
                removed += 1
    return added, removed


# ─── GitHub Issues (요구사항 문서 출처) ─────────────────────────────────
#
# 컨텍스트 블레임의 '요구사항 문서 출처' 는 더 이상 Bedrock KB 가 아니라
# 커밋이 속한 PR 본문이 가리키는 GitHub Issue 의 첨부 링크로 채운다.
# PR 본문은 vcs.find_pr_for_commit 이 이미 가져오므로(`PullRequest.body`),
# 호출 측은 그 body 만 넘기면 추가 PR 조회 없이 issue 까지 풀어낼 수 있다.

# "Closes #12", "fix: resolves #34", "Related to GH-56" 등에서 이슈 번호를 뽑는다.
# 단독 "#N" 도 허용하되, conventional commit 의 "feat[blame]: #2 …" 처럼
# 본문 첫 머리만 봐도 의도가 분명한 경우를 위해 별도 키워드 없이도 매칭한다.
_ISSUE_REF_RE = re.compile(
    r"(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?|ref(?:s|erence[sd]?)?|related\s+to)?\s*"
    r"(?:#|GH-)(\d+)",
    re.IGNORECASE,
)

# 첨부 후보 URL 패턴.
# (1) GitHub user-attachments (드래그-드롭 업로드): files/, assets/
# (2) 레포 raw / blob 의 파일 링크 (기획서를 레포에 같이 두는 케이스)
_ATTACHMENT_URL_RES = (
    re.compile(r"https?://github\.com/user-attachments/(?:files|assets)/[^\s)\]]+", re.IGNORECASE),
    re.compile(r"https?://[^\s)\]]+\.(?:pdf|docx?|xlsx?|pptx?|hwp|md|txt)", re.IGNORECASE),
)

# 마크다운 링크 `[label](url)` — label 이 첨부 표시명으로 더 친절하다.
_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")


def find_issues_from_pr_body(remote: Remote, pr_body: str) -> list[Issue]:
    """PR 본문에서 연결된 이슈 번호를 뽑고, 각 이슈 본문의 첨부 링크까지 채워 돌려준다.

    실패하거나 매칭이 없으면 빈 리스트 — 사이드바는 해당 영역만 비우고 진행한다.
    GitLab/그 외 호스트는 이번 범위 밖(빈 리스트).
    """
    if remote.host != "github" or not pr_body:
        return []

    numbers = _extract_issue_numbers(pr_body)
    if not numbers:
        return []

    headers = _github_headers()
    issues: list[Issue] = []
    for n in numbers:
        try:
            payload = _get_json(
                f"{remote.base}/repos/{remote.owner}/{remote.repo}/issues/{n}",
                headers,
            )
        except (urllib.error.URLError, KeyError, ValueError, TimeoutError):
            continue
        if not isinstance(payload, dict):
            continue
        body = payload.get("body") or ""
        issues.append(
            Issue(
                number=n,
                title=payload.get("title", ""),
                url=payload.get("html_url", ""),
                body=body,
                attachments=_extract_attachments(body),
            )
        )
    return issues


def _extract_issue_numbers(text: str) -> list[int]:
    """PR 본문에서 이슈 번호를 순서 보존·중복 제거하며 추출."""
    seen: set[int] = set()
    out: list[int] = []
    for m in _ISSUE_REF_RE.finditer(text):
        try:
            n = int(m.group(1))
        except (ValueError, TypeError):
            continue
        if n in seen:
            continue
        seen.add(n)
        out.append(n)
    return out


def _extract_attachments(body: str) -> list[Attachment]:
    """이슈 본문에서 첨부 링크를 뽑는다.

    우선순위: 마크다운 링크(라벨이 있어 친절) → 본문 안 raw URL 패턴(라벨은 파일명).
    중복 URL 은 한 번만 담는다.
    """
    if not body:
        return []

    seen: set[str] = set()
    out: list[Attachment] = []

    for m in _MD_LINK_RE.finditer(body):
        label, url = m.group(1).strip(), m.group(2)
        if url in seen or not _looks_like_attachment(url):
            continue
        seen.add(url)
        out.append(Attachment(label=label or _filename_from_url(url), url=url))

    for pat in _ATTACHMENT_URL_RES:
        for m in pat.finditer(body):
            url = m.group(0)
            if url in seen:
                continue
            seen.add(url)
            out.append(Attachment(label=_filename_from_url(url), url=url))

    return out


def _looks_like_attachment(url: str) -> bool:
    for pat in _ATTACHMENT_URL_RES:
        if pat.search(url):
            return True
    return False


def _filename_from_url(url: str) -> str:
    tail = url.rsplit("/", 1)[-1]
    return urllib.parse.unquote(tail) or url


# ─── GitHub Issues 검색 (역추적 ticket/semantic 폴백) ──────────────────────────

def search_github_issues(remote: Remote, query: str, per_page: int = 5) -> list[Issue]:
    """GitHub Issues를 키워드/티켓 번호로 검색해 반환한다.

    - ticket 매칭: query가 "PAY-2041" 같은 티켓이면 issue 제목·본문에서 정확 검색
    - semantic 매칭: 커밋 키워드로 관련 Issue를 근사 검색

    GitHub 미연동(토큰 없음·API 실패)이면 빈 리스트 — 호출 측이 그대로 진행한다.
    """
    if remote.host != "github" or not query.strip():
        return []

    try:
        headers = _github_headers()
        encoded = urllib.parse.quote(f"repo:{remote.owner}/{remote.repo} {query} is:issue")
        items = _get_json(
            f"https://api.github.com/search/issues?q={encoded}&per_page={per_page}",
            headers,
        )
        if not isinstance(items, dict):
            return []
        results: list[Issue] = []
        for item in items.get("items", []):
            body = item.get("body") or ""
            results.append(
                Issue(
                    number=item["number"],
                    title=item.get("title", ""),
                    url=item.get("html_url", ""),
                    body=body,
                    attachments=_extract_attachments(body),
                )
            )
        return results
    except (urllib.error.URLError, KeyError, ValueError, TimeoutError):
        return []


def find_issues_for_commit(repo_path: str, commit_hash: str, commit_message: str) -> tuple[list[Issue], str]:
    """커밋에서 연관 Issue를 최선으로 찾아 반환한다.

    반환: (issues, matchType)
      - ("issue", issues): PR 본문 → Issue 직접 연결
      - ("ticket", issues): 커밋 메시지 티켓 번호 → Issue 검색
      - ("semantic", issues): 키워드로 관련 Issue 검색
      - ("", []): 아무것도 없음

    traceability/service.py 에서만 사용한다.
    """
    remote = detect_remote(repo_path)
    if not remote:
        return [], ""

    # 1. PR → Issue 직접 연결
    try:
        pr = find_pr_for_commit(repo_path, commit_hash)
        if pr and pr.body:
            issues = find_issues_from_pr_body(remote, pr.body)
            if issues:
                return issues, "issue"
    except Exception:
        pass

    # 2. 커밋 메시지 티켓 번호로 Issue 검색
    from app.core.tickets import extract_ticket
    from app.core import git as _git
    try:
        branch = _git.get_current_branch(repo_path)
    except Exception:
        branch = ""
    ticket = extract_ticket(commit_message, branch)
    if ticket:
        issues = search_github_issues(remote, ticket)
        if issues:
            return issues, "ticket"

    # 3. 키워드로 관련 Issue 시맨틱 검색
    from app.features.blame.service import extract_keywords
    keywords = extract_keywords(commit_message)
    if keywords:
        query = " ".join(keywords[:5])  # 상위 5개 키워드
        issues = search_github_issues(remote, query, per_page=3)
        if issues:
            return issues, "semantic"

    return [], ""
