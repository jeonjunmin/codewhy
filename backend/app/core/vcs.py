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
