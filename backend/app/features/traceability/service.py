"""Requirement Trace 비즈니스 로직.

코드 라인 → blamed 커밋 → PR → GitHub Issue → 첨부 파일 경로로 연관 기획 문서를 찾는다.

추적 전략 (신뢰도 높은 순):
  1. issue    : commit → PR 본문 → Issue 직접 연결, 첨부 파일 있음 (확정)
  2. ticket   : 커밋 메시지의 티켓 번호(PAY-2041)로 GitHub Issue 검색 (높음)
  3. semantic : 커밋 키워드로 GitHub Issues 검색 (추정)

각 결과에 matchType 과 confidence 를 실어 UI 가 신뢰도를 구분 표시한다.

설계 원칙: 모든 외부 연동 실패는 빈 결과 → 로컬에서 절대 깨지지 않는다.

👤 담당: 개발자 C
"""

import asyncio
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from app.core import vcs
from app.core.tickets import extract_issue_numbers, extract_ticket, extract_tickets
from app.core.vcs import Issue
from app.db import crud_common
from app.features.traceability import crud


_MAX_EXCERPT_CHARS = 300
_MAX_BODY_CHARS = 4000   # 상세 화면 본문 전문 상한(과도한 payload 방지)


# 매치 신뢰도 순위 — 같은 이슈가 여러 커밋에서 다른 경로로 잡힐 때 더 강한 근거를 남긴다.
_MATCH_RANK = {"issue": 3, "ticket": 2, "semantic": 1}


def trace(commit_hash: str, commit_message: str, *, branch: str, remote) -> list[dict]:
    """단일 커밋(blamed 커밋)을 기준으로 연관 기획 문서를 반환한다(커밋 이력 폴백용).

    git 실행은 확장이 끝냈고, 여기서는 받은 데이터(커밋 해시/메시지/브랜치)와
    파싱된 remote 로 GitHub API 만 조회한다.
    """
    issues, match_type = vcs.find_issues_for_remote(remote, commit_hash, commit_message, branch)
    if not issues:
        return []

    return _format_results(issues, match_type)


async def trace_file(
    db: AsyncSession,
    *,
    repo_id: int,
    file_id: int,
    commits,
    branch: str,
    remote,
    max_commits: int = 20,
) -> list[dict]:
    """파일을 건드린 커밋들 전체에서 연관 이슈를 모아 '파일 단위' 목록을 만든다.

    2계층 캐시로 GitHub API 호출을 커밋 수 비례 → 거의 상수로 줄인다:
      1. 커밋↔이슈 연결(불변) — commit_issues 에 영구 캐시. 미인덱싱 커밋만 조회(cache-aside).
         티켓 경로는 search_github_issues_batch 로 묶어 호출 수를 더 줄인다.
      2. 이슈 메타(가변) — 캐시하지 않고 번호 집합으로 매 요청 일괄 refresh(항상 최신).
    """
    if not commits:
        return []
    selected = commits[:max_commits]

    # 1) 커밋 upsert + 파일 연결(공유 백본). commit_hash → commit_id 매핑 확보.
    hash_to_id = await crud_common.upsert_commits_bulk(
        db, repo_id, [_commit_to_row(c) for c in selected]
    )
    commit_ids = list(hash_to_id.values())
    await crud_common.link_commits_files_bulk(db, commit_ids, file_id)

    # 2) 미인덱싱 커밋만 추림 — 이미 연결을 추출한 커밋은 다시 GitHub 에 묻지 않는다.
    uncached = set(await crud.get_uncached_commit_ids(db, commit_ids))

    # 3) 미인덱싱 커밋의 연결을 GitHub 에서 추출(블로킹 → to_thread) 후 영구 저장.
    if uncached and remote:
        targets = [
            (hash_to_id[c.hash], c.hash, c.subject)
            for c in selected
            if hash_to_id.get(c.hash) in uncached
        ]
        links_by_commit = await asyncio.to_thread(
            _extract_links_for_commits, remote, targets
        )
        for commit_id, links in links_by_commit.items():
            await crud.save_commit_issues(db, commit_id, links)  # 0건도 인덱싱 표시

    # 4) 파일 전체 연결 수집 → 이슈별 최강 link_source 로 dedup.
    best: dict[int, str] = {}
    for number, source, _conf in await crud.get_issue_links_for_file(db, file_id):
        if number not in best or _MATCH_RANK.get(source, 0) > _MATCH_RANK.get(best[number], 0):
            best[number] = source
    if not best:
        await db.commit()
        return []

    # 5) 이슈 메타 일괄 refresh(캐시 안 함 = 항상 최신) → DocumentMatch 변환.
    meta = await asyncio.to_thread(vcs.fetch_issues_batch, remote, list(best.keys()))
    await db.commit()

    results = [
        _format_one(meta[number], source)
        for number, source in best.items()
        if number in meta
    ]
    results.sort(key=lambda d: (-_MATCH_RANK.get(d["matchType"], 0), -(d["issueNumber"] or 0)))
    return results


def _commit_to_row(c) -> dict:
    """CommitInput → upsert_commits_bulk 가 받는 dict(이미 파싱된 값)로 변환한다."""
    return {
        "commit_hash": c.hash,
        "author": c.author or None,
        "committed_date": _parse_iso_date(c.date),
        "message": c.subject or None,
        "ticket": extract_ticket(c.subject or ""),
    }


def _parse_iso_date(s: str | None) -> date | None:
    try:
        return date.fromisoformat((s or "")[:10])
    except ValueError:
        return None


def _extract_links_for_commits(remote, targets: list[tuple[int, str, str]]) -> dict[int, list[dict]]:
    """미인덱싱 커밋들의 연관 이슈 연결을 GitHub 에서 추출한다(순수 동기 — to_thread 로 호출).

    반환: {commit_id: [{"issue_number","link_source","confidence"}, ...]} — 0건 커밋도 키로 포함.
    PR 직결(issue)을 티켓보다 먼저 등록해, 같은 이슈가 두 경로로 잡히면 더 강한 근거를 남긴다.
    """
    links: dict[int, list[dict]] = {cid: [] for cid, _h, _s in targets}
    seen: dict[int, set[int]] = {cid: set() for cid, _h, _s in targets}

    def add(cid: int, number: int, source: str, confidence):
        if number in seen[cid]:
            return
        seen[cid].add(number)
        links[cid].append({"issue_number": number, "link_source": source, "confidence": confidence})

    # ── 직접 참조 경로(issue, 확정) — 커밋 메시지의 #N + 머지된 PR 본문의 #N. ──
    #    커밋 메시지가 "#46 ..." 형태면 토큰 없이도 /issues/N 으로 메타를 읽으므로
    #    티켓(PAY-xxx)을 안 쓰는 저장소에서도 동작한다. 가장 강한 근거라 먼저 등록.
    for cid, commit_hash, subject in targets:
        for n in extract_issue_numbers(subject or ""):
            add(cid, n, "issue", None)
        try:
            pr = vcs.find_pr_for_remote(remote, commit_hash)
        except Exception:
            pr = None
        if pr and pr.body:
            for n in extract_issue_numbers(pr.body):
                add(cid, n, "issue", None)

    # ── 티켓 경로(ticket) — 미인덱싱 커밋 전체의 티켓을 모아 OR 쿼리로 한 번에 검색. ──
    ticket_to_commits: dict[str, list[int]] = {}
    for cid, _commit_hash, subject in targets:
        for ticket in extract_tickets(subject or ""):
            ticket_to_commits.setdefault(ticket, []).append(cid)
    if ticket_to_commits:
        found = vcs.search_github_issues_batch(remote, list(ticket_to_commits))
        for ticket, issues in found.items():
            for cid in ticket_to_commits.get(ticket, []):
                for issue in issues:
                    add(cid, issue.number, "ticket", 0.8)

    return links


def _confidence_for(match_type: str) -> float | None:
    """matchType 별 신뢰도. issue=확정(None), ticket=0.8, semantic=0.5."""
    if match_type == "issue":
        return None
    return 0.8 if match_type == "ticket" else 0.5


def _format_results(issues: list[Issue], match_type: str) -> list[dict]:
    """동일 match_type 의 Issue 목록을 DocumentMatch 형식으로 변환한다(단일-커밋 경로)."""
    return [_format_one(issue, match_type) for issue in issues]


def _format_one(issue: Issue, match_type: str) -> dict:
    """Issue 한 건을 TraceResponse DocumentMatch(이슈 단위) 형식으로 변환한다.

    한 이슈가 하나의 항목이며, 첨부 문서는 항목 안의 attachments 배열로 중첩한다.
    (상세 화면이 이슈 메타 + 첨부 목록을 함께 그리기 때문.)
    """
    return {
        "title": issue.title or f"Issue #{issue.number}",
        "url": issue.url,
        "matchType": match_type,
        "confidence": _confidence_for(match_type),
        "excerpt": _make_excerpt(issue.body),
        "issueNumber": issue.number,
        "state": issue.state or None,
        "labels": issue.labels,
        "assignee": issue.assignee or None,
        "createdAt": issue.created_at or None,
        "updatedAt": issue.updated_at or None,
        "commentCount": issue.comment_count,
        "body": _clip(issue.body, _MAX_BODY_CHARS),
        "attachments": [
            {"label": att.label, "url": att.url, "pageCount": att.page_count}
            for att in issue.attachments
        ],
    }


def _clip(text: str, limit: int) -> str | None:
    if not text or not text.strip():
        return None
    stripped = text.strip()
    return stripped[:limit] + "…" if len(stripped) > limit else stripped


def _make_excerpt(body: str) -> str | None:
    if not body or not body.strip():
        return None
    stripped = body.strip()
    if len(stripped) > _MAX_EXCERPT_CHARS:
        return stripped[:_MAX_EXCERPT_CHARS] + "…"
    return stripped
