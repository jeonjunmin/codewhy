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
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from functools import lru_cache, wraps

from app.core.config import (
    get_attachment_domain_allowlist,
    get_github_token,
    get_gitlab_token,
)

_TIMEOUT = 6  # 초 — 호스팅 API 가 느려도 블레임 응답을 오래 잡지 않는다

# §6 공통 처리 원칙 #4 — GitHub PR/Issue 메모이즈 캐시 크기.
# 라인 클릭마다 같은 PR·Issue 를 재조회하는 비용을 차단한다. 128은 한 세션의
# 활성 PR/Issue 수보다 넉넉히 큰 값. 프로세스 재시작 시 자연 무효화.
_VCS_CACHE_SIZE = 128

# 이슈/커밋별 GitHub 왕복을 동시에 묶을 스레드 수. 각 호출은 urllib 블로킹이라
# 순차로 돌면 건수만큼 누적되는데, 동시에 띄우면 사실상 가장 느린 한 건에 수렴한다.
# 8은 GitHub 2차 rate limit(동시 요청 과다)을 자극하지 않는 보수적인 상한.
_FETCH_WORKERS = 8


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
    # 페이지 수 — PDF 등에서만. 첨부 바이너리를 매 trace 마다 내려받지는 않으므로
    # 보통 None(미상). 추후 지연 추출을 붙이면 채운다.
    page_count: int | None = None


@dataclass
class Comment:
    """이슈 활동 타임라인 한 항목 — 사람 코멘트와 시스템 이벤트를 한 모델로 담는다.

    kind 으로 종류를 가른다:
      - "comment": 사람이 단 코멘트. author/body/created_at/attachments 사용.
      - "event"  : 시스템 이벤트. author(=행위자)/created_at + event 종류별 필드 사용.
                   event 값: labeled / assigned / committed / referenced / closed / reopened
    프론트는 같은 행위자의 연속 event 를 한 문장으로 묶어 그리므로(예: "…라벨을 추가하고
    …담당자로 지정"), 백엔드는 한글 문장을 만들지 않고 구조화된 필드만 싣는다.
    """
    kind: str                                    # "comment" | "event"
    author: str = ""                             # 작성자(comment) 또는 행위자(event)
    created_at: str = ""                         # ISO8601
    body: str = ""                               # comment 본문
    event: str = ""                              # event 종류
    label: str = ""                              # labeled 이벤트의 라벨명
    assignee: str = ""                           # assigned 이벤트의 담당자
    commit_sha: str = ""                         # committed/referenced 이벤트의 커밋 해시
    commit_summary: str = ""                     # 커밋 메시지 첫 줄
    attachments: list[Attachment] = field(default_factory=list)


@dataclass
class Issue:
    """커밋에 연결된 GitHub Issue 한 건."""
    number: int
    title: str
    url: str
    body: str = ""
    attachments: list[Attachment] = field(default_factory=list)
    # ── 상세 화면(이슈 탭)용 메타 — GitHub/GitLab payload 에서 채운다 ──────────
    state: str = ""                              # open / closed
    labels: list[str] = field(default_factory=list)
    assignee: str = ""                           # 담당자 로그인/표시명 (없으면 "")
    created_at: str = ""                         # ISO8601 (개설일)
    updated_at: str = ""                         # ISO8601 (최근 수정)
    comment_count: int = 0                       # 코멘트 수(본문은 미페치)
    comments: list[Comment] = field(default_factory=list)  # 활동 타임라인(코멘트+이벤트)


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


def parse_remote(url: str | None) -> Remote | None:
    """remote URL 원문(확장이 `git remote get-url origin` 으로 보낸 것)을 파싱한다.

    원격 백엔드에는 로컬 저장소가 없어 detect_remote(=git 호출)를 쓸 수 없으므로,
    확장이 보낸 URL 문자열에서 host/owner/repo 를 뽑는다.
    """
    if not url:
        return None
    return _parse_remote_url(url)


def find_pr_for_remote(remote: Remote | None, commit_hash: str) -> PullRequest | None:
    """이미 파싱된 remote 로 커밋의 PR/MR 을 찾는다(git 호출 없음 — 원격 백엔드용).

    어느 단계든 실패하면 None — 호출 측이 PR 정보 없이 진행한다.
    """
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


def find_pr_for_commit(repo_path: str, commit_hash: str) -> PullRequest | None:
    """커밋이 속한 PR/MR 을 찾아 변경 통계까지 채워 반환한다(로컬 git 으로 remote 감지).

    어느 단계든 실패하면 None — 호출 측이 PR 정보 없이 진행한다.
    """
    return find_pr_for_remote(detect_remote(repo_path), commit_hash)


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


# ─── 단기 TTL 캐시 ────────────────────────────────────────────────────
# 이슈 메타/타임라인은 가변(상태·라벨·담당자·코멘트)이라 lru_cache 로 영구 캐시할 수 없다.
# 하지만 IDE 사이드바 열람 시나리오에선 초 단위 staleness 가 체감되지 않으므로,
# '항상 최신(0초)' 대신 짧은 TTL 로 웜 경로(재조회·스크롤 재렌더·이슈 간 이동)를 즉답화한다.
_META_TTL = 30        # 초 — 이슈 메타/타임라인 캐시 수명
_META_CACHE_MAX = 256  # 항목 상한 — 만료분부터, 그래도 차면 가장 오래된 것부터 제거


def _ttl_cache(ttl: float, maxsize: int = _META_CACHE_MAX):
    """인자(args) 기준 TTL 메모이즈 데코레이터.

    lru_cache 와 달리 ttl 초가 지나면 다시 조회한다. _FETCH_WORKERS 스레드 풀에서
    동시 호출되므로 락으로 보호한다(콜드 레이스 시 중복 페치가 날 순 있으나 무해).
    """
    def decorator(fn):
        store: dict = {}
        lock = threading.Lock()

        @wraps(fn)
        def wrapper(*args):
            now = time.monotonic()
            with lock:
                hit = store.get(args)
                if hit is not None and now - hit[0] < ttl:
                    return hit[1]
            value = fn(*args)
            with lock:
                if len(store) >= maxsize:
                    for k in [k for k, (ts, _v) in store.items() if now - ts >= ttl]:
                        del store[k]
                    if len(store) >= maxsize:
                        del store[min(store, key=lambda k: store[k][0])]
                store[args] = (now, value)
            return value

        wrapper.cache_clear = store.clear  # 테스트/디버그용
        return wrapper
    return decorator


# ─── GitHub ─────────────────────────────────────────────────────────
def _github_headers() -> dict:
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "codewhy"}
    token = get_github_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _github_pr_for_commit(remote: Remote, commit_hash: str) -> PullRequest | None:
    listing = _github_pr_listing_for_commit(remote.base, remote.owner, remote.repo, commit_hash)
    if not listing:
        return None
    pr = listing[0]  # 가장 연관 높은 PR
    number = pr["number"]
    pr_url = pr.get("html_url", "")

    detail = _github_pr_detail(remote.base, remote.owner, remote.repo, number)
    files_json = _github_pr_files(remote.base, remote.owner, remote.repo, number)
    files = (
        [
            ChangedFile(path=f["filename"], added=f.get("additions", 0), status=f.get("status", ""))
            for f in files_json
        ]
        if isinstance(files_json, list)
        else []
    )

    return PullRequest(
        url=pr_url,
        number=number,
        title=detail.get("title", ""),
        body=detail.get("body") or "",
        added=detail.get("additions", 0),
        removed=detail.get("deletions", 0),
        files=files,
    )


# ─── GitHub API 메모이즈 (§6 공통 처리 원칙 #4) ───────────────────────────
# 라인 클릭마다 같은 PR·Issue 를 재조회하는 것을 차단한다.
# - 인자는 str/int (hashable)
# - 반환은 응답 JSON 의 안전한 사본(list 는 tuple 안의 frozen dict 대신 list 그대로 — 호출 측이
#   읽기 전용으로만 사용한다는 전제). 호출 측에서 in-place 수정 금지.
# - 캐시 무효화는 프로세스 재시작 시점 — PR/Issue 본문은 빈번히 바뀌지 않으므로 충분.

@lru_cache(maxsize=_VCS_CACHE_SIZE)
def _github_pr_listing_for_commit(base: str, owner: str, repo: str, commit_hash: str) -> tuple[dict, ...]:
    """커밋이 속한 PR 목록 (가장 연관도 높은 PR 1건만 사용). 빈 결과면 빈 튜플."""
    payload = _get_json(f"{base}/repos/{owner}/{repo}/commits/{commit_hash}/pulls", _github_headers())
    return tuple(payload) if isinstance(payload, list) else ()


@lru_cache(maxsize=_VCS_CACHE_SIZE)
def _github_pr_detail(base: str, owner: str, repo: str, number: int) -> dict:
    """PR 상세 (title/body/additions/deletions). 실패 시 빈 dict."""
    payload = _get_json(f"{base}/repos/{owner}/{repo}/pulls/{number}", _github_headers())
    return payload if isinstance(payload, dict) else {}


_PR_FILES_PAGE_SIZE = 100
_PR_FILES_MAX_PAGES = 3  # 최대 300건. 더 큰 PR은 "외 N건" 표기로 안내한다.


@lru_cache(maxsize=_VCS_CACHE_SIZE)
def _github_pr_files(base: str, owner: str, repo: str, number: int) -> tuple[dict, ...]:
    """PR 변경 파일 목록(최대 _PR_FILES_MAX_PAGES 페이지). 빈 결과면 빈 튜플.

    GitHub은 페이지당 최대 100건. 마지막 페이지가 페이지 사이즈보다 작거나 빈
    페이지가 나오면 조회를 중단한다.
    """
    headers = _github_headers()
    collected: list[dict] = []
    for page in range(1, _PR_FILES_MAX_PAGES + 1):
        url = (
            f"{base}/repos/{owner}/{repo}/pulls/{number}/files"
            f"?per_page={_PR_FILES_PAGE_SIZE}&page={page}"
        )
        try:
            payload = _get_json(url, headers)
        except (urllib.error.URLError, KeyError, ValueError, TimeoutError):
            break
        if not isinstance(payload, list) or not payload:
            break
        collected.extend(payload)
        if len(payload) < _PR_FILES_PAGE_SIZE:
            break
    return tuple(collected)


@_ttl_cache(_META_TTL)
def _github_issue(base: str, owner: str, repo: str, number: int) -> dict:
    """Issue 본문/메타(상태/라벨/담당자/코멘트수) 페치. 실패 시 빈 dict.

    같은 응답에 본문(거의 불변)과 가변 메타(담당자/라벨/상태)가 함께 온다. 영구 캐시하면
    담당자를 나중에 지정해도 옛 결과가 고정돼 '미지정'으로 보이므로, _META_TTL(초) 만큼만
    캐시해 신선도를 유지하면서 웜 경로 재조회를 즉답화한다.
    """
    payload = _get_json(f"{base}/repos/{owner}/{repo}/issues/{number}", _github_headers())
    return payload if isinstance(payload, dict) else {}


# 상세 화면 타임라인에 그릴 이벤트만 추린다(닫힘/재오픈/라벨/담당자/커밋).
# 그 외(subscribed, mentioned, renamed 등)는 노이즈라 버린다.
_GH_TIMELINE_EVENTS = {"commented", "labeled", "assigned", "committed", "referenced", "closed", "reopened"}


@_ttl_cache(_META_TTL)
def _github_issue_timeline(base: str, owner: str, repo: str, number: int) -> list:
    """이슈 활동 타임라인(코멘트+이벤트)을 페치해 Comment 리스트로 반환.

    GitHub Timeline API(/issues/{n}/timeline)는 코멘트와 시스템 이벤트를 시간순
    한 배열로 준다 — 상세 화면의 활동 피드와 정확히 일치한다. 실패하면 빈 리스트.

    코멘트는 본문/첨부(거의 불변)와 달리 빈번히 추가되므로 영구 캐시할 수 없다.
    _META_TTL(초) 만큼만 캐시해, 단 직후가 아니면 최신을 보장하면서 재렌더를 즉답화한다.
    """
    headers = {**_github_headers(), "Accept": "application/vnd.github+json"}
    url = f"{base}/repos/{owner}/{repo}/issues/{number}/timeline?per_page=100"
    try:
        payload = _get_json(url, headers)
    except (urllib.error.URLError, KeyError, ValueError, TimeoutError):
        return []
    if not isinstance(payload, list):
        return []
    out: list[Comment] = []
    for ev in payload:
        if not isinstance(ev, dict):
            continue
        parsed = _github_timeline_entry(ev)
        if parsed is None:
            continue
        # referenced 는 sha 만 주므로, 커밋 메시지 첫 줄을 가볍게 보강한다(이미지의 "… 반영" 줄).
        if parsed.event == "referenced" and parsed.commit_sha and not parsed.commit_summary:
            parsed.commit_summary = _github_commit_summary(base, owner, repo, parsed.commit_sha)
        out.append(parsed)
    return out


def _github_timeline_entry(ev: dict) -> "Comment | None":
    """Timeline payload 한 항목 → Comment. 관심 없는 이벤트면 None."""
    kind = ev.get("event")
    if kind not in _GH_TIMELINE_EVENTS:
        return None

    if kind == "commented":
        body = ev.get("body") or ""
        return Comment(
            kind="comment",
            author=_github_actor_login(ev.get("user")),
            created_at=ev.get("created_at", ""),
            body=body,
            attachments=_extract_attachments(body),
        )

    actor = _github_actor_login(ev.get("actor"))
    if kind == "labeled":
        label = ev.get("label") or {}
        return Comment(kind="event", event="labeled", author=actor,
                       created_at=ev.get("created_at", ""),
                       label=label.get("name", "") if isinstance(label, dict) else str(label))
    if kind == "assigned":
        return Comment(kind="event", event="assigned", author=actor,
                       created_at=ev.get("created_at", ""),
                       assignee=_github_actor_login(ev.get("assignee")))
    if kind in ("closed", "reopened"):
        return Comment(kind="event", event=kind, author=actor, created_at=ev.get("created_at", ""))
    if kind == "committed":
        # 'committed' 는 author/committer 가 git 신원(dict, login 없음), 메시지 전문을 준다.
        author_meta = ev.get("author") or {}
        message = ev.get("message") or ""
        return Comment(kind="event", event="committed",
                       author=author_meta.get("name", "") if isinstance(author_meta, dict) else "",
                       created_at=author_meta.get("date", "") if isinstance(author_meta, dict) else "",
                       commit_sha=ev.get("sha", ""), commit_summary=message.splitlines()[0] if message else "")
    if kind == "referenced":
        # 커밋이 이슈를 참조 — sha 만 있고 메시지는 없다(추가 페치 없이 sha 만 표기).
        return Comment(kind="event", event="referenced", author=actor,
                       created_at=ev.get("created_at", ""), commit_sha=ev.get("commit_id", ""))
    return None


def _github_actor_login(actor: object) -> str:
    return actor.get("login", "") if isinstance(actor, dict) else ""


@lru_cache(maxsize=_VCS_CACHE_SIZE)
def _github_commit_summary(base: str, owner: str, repo: str, sha: str) -> str:
    """커밋 메시지 첫 줄만 가볍게 페치(referenced 이벤트는 sha 만 줘서 보강용). 실패 시 ""."""
    if not sha:
        return ""
    try:
        payload = _get_json(f"{base}/repos/{owner}/{repo}/commits/{sha}", _github_headers())
    except (urllib.error.URLError, KeyError, ValueError, TimeoutError):
        return ""
    commit = payload.get("commit") if isinstance(payload, dict) else None
    message = commit.get("message", "") if isinstance(commit, dict) else ""
    return message.splitlines()[0] if message else ""


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
    # 종료 문자에 따옴표/꺾쇠도 포함 — HTML <img src="URL"> 에서 닫는 따옴표가 URL 에 섞이지 않게.
    re.compile(r'https?://github\.com/user-attachments/(?:files|assets)/[^\s)\]"\'<>]+', re.IGNORECASE),
    # 문서 + 이미지 확장자. 이미지는 프런트엔드가 인라인 미리보기로 그린다(sidebar.ts renderImageAttachment).
    re.compile(r'https?://[^\s)\]"\'<>]+\.(?:pdf|docx?|xlsx?|pptx?|hwp|md|txt|png|jpe?g|gif|webp|svg|bmp|avif)', re.IGNORECASE),
)

# 마크다운 링크 `[label](url)` — label 이 첨부 표시명으로 더 친절하다.
_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")


def find_issues_from_pr_body(remote: Remote, pr_body: str) -> list[Issue]:
    """PR/MR 본문에서 연결된 이슈 번호를 뽑고, 각 이슈 본문의 첨부 링크까지 채워 돌려준다.

    실패하거나 매칭이 없으면 빈 리스트 — 사이드바는 해당 영역만 비우고 진행한다.
    GitHub·GitLab 모두 지원. 그 외 호스트는 빈 리스트.
    """
    if not pr_body:
        return []
    numbers = _extract_issue_numbers(pr_body)
    if not numbers:
        return []
    return _fetch_issues(remote, numbers)


def find_issues_from_commit_message(remote: Remote, commit_message: str) -> list[Issue]:
    """PR/MR 본문이 비거나 매칭이 안 됐을 때 커밋 메시지에서 직접 #N 패턴을 찾는 2차 폴백.

    Squash/Rebase 후 PR 본문이 사라지거나, PR 자체가 없는 직접 푸시 커밋도 살린다.
    """
    if not commit_message:
        return []
    numbers = _extract_issue_numbers(commit_message)
    if not numbers:
        return []
    return _fetch_issues(remote, numbers)


def _pick_login(single: object, multi: object, key: str) -> str:
    """담당자 1명을 고른다 — 단일 필드(assignee)가 비면 배열(assignees) 첫 명으로 폴백.

    GitHub/GitLab 모두 담당자를 단일+배열 두 필드로 준다. 보통 단일에도 첫 명이
    들어가지만, 멀티 지정 등으로 단일이 비는 케이스를 대비해 배열을 폴백으로 본다.
    """
    if isinstance(single, dict) and single.get(key):
        return single[key]
    for cand in (multi or []):
        if isinstance(cand, dict) and cand.get(key):
            return cand[key]
    return ""


def _issue_from_github(payload: dict, number: int) -> Issue:
    """GitHub Issue/Search payload → Issue. 상세 화면 메타까지 채운다."""
    body = payload.get("body") or ""
    labels = [
        (lab.get("name") if isinstance(lab, dict) else str(lab))
        for lab in (payload.get("labels") or [])
    ]
    return Issue(
        number=number,
        title=payload.get("title", ""),
        url=payload.get("html_url", ""),
        body=body,
        attachments=_extract_attachments(body),
        state=payload.get("state", ""),
        labels=[l for l in labels if l],
        assignee=_pick_login(payload.get("assignee"), payload.get("assignees"), "login"),
        created_at=payload.get("created_at", ""),
        updated_at=payload.get("updated_at", ""),
        comment_count=int(payload.get("comments") or 0),
    )


def _issue_from_gitlab(payload: dict, iid: int) -> Issue:
    """GitLab Issue payload → Issue. 상세 화면 메타까지 채운다."""
    body = payload.get("description") or ""
    return Issue(
        number=iid,
        title=payload.get("title", ""),
        url=payload.get("web_url", ""),
        body=body,
        attachments=_extract_attachments(body),
        state=payload.get("state", ""),
        labels=[str(l) for l in (payload.get("labels") or []) if l],
        assignee=_pick_login(payload.get("assignee"), payload.get("assignees"), "username"),
        created_at=payload.get("created_at", ""),
        updated_at=payload.get("updated_at", ""),
        comment_count=int(payload.get("user_notes_count") or 0),
    )


def _fetch_one_issue(remote: Remote, n: int) -> "Issue | None":
    """이슈 한 건의 본문·첨부·타임라인을 채워 Issue 로 반환. 실패/빈 결과면 None.

    이슈끼리 독립적인 네트워크 왕복이라 _fetch_issues 가 이 함수를 스레드로 병렬 호출한다.
    """
    try:
        if remote.host == "github":
            payload = _github_issue(remote.base, remote.owner, remote.repo, n)
            if not payload:
                return None
            issue = _issue_from_github(payload, n)
            issue.comments = _github_issue_timeline(remote.base, remote.owner, remote.repo, n)
            return issue
        if remote.host == "gitlab":
            payload = _gitlab_issue(remote.base, remote.owner, remote.repo, n)
            if not payload:
                return None
            issue = _issue_from_gitlab(payload, n)
            issue.comments = _gitlab_issue_notes(remote.base, remote.owner, remote.repo, n)
            return issue
    except (urllib.error.URLError, KeyError, ValueError, TimeoutError):
        return None
    return None


def _fetch_issues(remote: Remote, numbers: list[int]) -> list[Issue]:
    """이슈 번호 목록으로 GitHub/GitLab 에서 본문·첨부를 채워 Issue 객체로 반환.

    번호당 (이슈 메타 + 타임라인) 왕복이 발생하므로 순차로 돌면 건수만큼 누적된다.
    이슈끼리 독립적이라 스레드 풀로 동시에 띄워 전체 지연을 가장 느린 한 건 수준으로 줄인다.
    """
    if not numbers:
        return []
    workers = min(_FETCH_WORKERS, len(numbers))
    with ThreadPoolExecutor(max_workers=workers) as ex:
        return [issue for issue in ex.map(lambda n: _fetch_one_issue(remote, n), numbers) if issue]


@_ttl_cache(_META_TTL)
def _gitlab_issue(base: str, owner: str, repo: str, iid: int) -> dict:
    """GitLab Issue 본문/메타 페치. 실패 시 빈 dict.

    _github_issue 와 같은 이유로 _META_TTL(초) 만큼만 캐시한다 — 메타 신선도와 웜 경로 속도의 타협.
    """
    project_id = urllib.parse.quote(f"{owner}/{repo}", safe="")
    payload = _get_json(f"{base}/projects/{project_id}/issues/{iid}", _gitlab_headers())
    return payload if isinstance(payload, dict) else {}


@_ttl_cache(_META_TTL)
def _gitlab_issue_notes(base: str, owner: str, repo: str, iid: int) -> list:
    """GitLab Issue notes(코멘트+시스템 노트) → Comment 리스트(시간순). 실패 시 빈 리스트.

    GitLab 은 라벨/담당자 변경도 system==True 인 note 로 남긴다(본문이 한글이 아닌
    영어 평문). GitHub timeline 처럼 구조가 분해돼 있지 않으므로, 시스템 노트는
    event="note" 로 두고 본문을 그대로 싣는다(프론트가 평문 한 줄로 그린다).

    GitHub 타임라인과 같은 이유로 _META_TTL(초) 만큼만 캐시한다 — 코멘트 신선도와 속도의 타협.
    """
    project_id = urllib.parse.quote(f"{owner}/{repo}", safe="")
    url = f"{base}/projects/{project_id}/issues/{iid}/notes?sort=asc&order_by=created_at&per_page=100"
    try:
        payload = _get_json(url, _gitlab_headers())
    except (urllib.error.URLError, KeyError, ValueError, TimeoutError):
        return []
    if not isinstance(payload, list):
        return []
    out: list[Comment] = []
    for note in payload:
        if not isinstance(note, dict):
            continue
        author = note.get("author") or {}
        login = author.get("username", "") if isinstance(author, dict) else ""
        body = note.get("body") or ""
        if note.get("system"):
            out.append(Comment(kind="event", event="note", author=login,
                               created_at=note.get("created_at", ""), body=body))
        else:
            out.append(Comment(kind="comment", author=login, created_at=note.get("created_at", ""),
                               body=body, attachments=_extract_attachments(body)))
    return out


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
    # 도메인 화이트리스트(Notion/Confluence/위키 등 확장자 없는 외부 링크)
    allowlist = get_attachment_domain_allowlist()
    if allowlist:
        try:
            host = urllib.parse.urlparse(url).hostname or ""
        except ValueError:
            return False
        host = host.lower()
        return any(host == d or host.endswith("." + d) for d in allowlist)
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
            results.append(_issue_from_github(item, item["number"]))
        return results
    except (urllib.error.URLError, KeyError, ValueError, TimeoutError):
        return []


# 한 번의 Search 쿼리에 묶을 티켓 수 상한 — q 길이/항 수 폭주 방어(많으면 청크로 분할).
_TICKETS_PER_QUERY = 20


def search_github_issues_batch(remote: Remote, tickets: list[str]) -> dict[str, list[Issue]]:
    """여러 티켓을 OR 쿼리로 묶어 한 번(또는 몇 번)에 검색한다 — 파일 단위 ticket 경로용.

    `repo:o/r (PAY-1 OR PAY-2 ...) is:issue` 형태로, 커밋마다 따로 검색하던 것을
    티켓 고유 집합 단위 한두 번으로 줄인다. 반환: {ticket: [Issue,...]}.

    GitHub Search 결과는 어떤 티켓이 매칭됐는지 알려주지 않으므로, 이슈 제목/본문에
    티켓 문자열이 들어있는지로 역매핑한다(보통 제목·본문에 티켓이 적혀 있음).
    미연동/실패 시 빈 dict — 호출 측이 그대로 진행한다.
    """
    out: dict[str, list[Issue]] = {}
    if remote.host != "github":
        return out
    uniq = [t for t in dict.fromkeys(tickets) if t.strip()]
    if not uniq:
        return out

    for start in range(0, len(uniq), _TICKETS_PER_QUERY):
        chunk = uniq[start:start + _TICKETS_PER_QUERY]
        or_expr = " OR ".join(chunk)
        issues = search_github_issues(remote, f"({or_expr})", per_page=100)
        # 역매핑 — 각 이슈를 자신을 매칭시킨 티켓(들)에 귀속.
        for issue in issues:
            haystack = f"{issue.title}\n{issue.body}".upper()
            for ticket in chunk:
                if ticket.upper() in haystack:
                    out.setdefault(ticket, []).append(issue)
    return out


def fetch_issues_batch(remote: Remote | None, numbers: list[int]) -> dict[int, Issue]:
    """이슈 번호 집합의 최신 메타를 일괄 조회한다 — 파일 단위 메타 refresh용.

    번호별 단건 조회(`_fetch_issues`)를 재사용하되, `@lru_cache` 가 세션 내 중복 호출을
    막는다. 메타(state/labels/commentCount)는 캐시하지 않고 매 요청 여기서 새로 읽어
    항상 최신을 유지한다(신선도 보장). 반환: {issue_number: Issue}.
    """
    if not remote or not numbers:
        return {}
    uniq = list(dict.fromkeys(numbers))
    return {issue.number: issue for issue in _fetch_issues(remote, uniq)}


def find_issues_for_remote(
    remote: "Remote | None", commit_hash: str, commit_message: str, branch: str = "",
) -> tuple[list[Issue], str]:
    """이미 파싱된 remote 로 커밋의 연관 Issue 를 찾는다(git 호출 없음 — 원격 백엔드용).

    remote/branch 는 확장이 로컬 git 으로 수집해 보낸 데이터에서 비롯한다.
    반환 규약은 find_issues_for_commit 과 동일하다.
    """
    if not remote:
        return [], ""

    # 1. PR → Issue 직접 연결
    try:
        pr = find_pr_for_remote(remote, commit_hash)
        if pr and pr.body:
            issues = find_issues_from_pr_body(remote, pr.body)
            if issues:
                return issues, "issue"
    except Exception:
        pass

    # 2. 커밋 메시지 티켓 번호로 Issue 검색
    from app.core.tickets import extract_ticket
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


def find_issues_for_commit(repo_path: str, commit_hash: str, commit_message: str) -> tuple[list[Issue], str]:
    """커밋에서 연관 Issue를 최선으로 찾아 반환한다(로컬 git 으로 remote/branch 감지).

    반환: (issues, matchType)
      - ("issue", issues): PR 본문 → Issue 직접 연결
      - ("ticket", issues): 커밋 메시지 티켓 번호 → Issue 검색
      - ("semantic", issues): 키워드로 관련 Issue 검색
      - ("", []): 아무것도 없음
    """
    from app.core import git as _git
    try:
        branch = _git.get_current_branch(repo_path)
    except Exception:
        branch = ""
    return find_issues_for_remote(detect_remote(repo_path), commit_hash, commit_message, branch)


