"""Requirement Trace 비즈니스 로직 (다층 추적).

코드 라인 → blamed 커밋에서 출발해, 신뢰도 높은 순으로 폴백하며 연관 기획 문서를 찾는다.
브라운필드(SM) 프로젝트는 커밋에 티켓이 없는 경우가 많아, 티켓 정확매칭 하나로는 빈 결과가 나기
쉽다. 그래서 사전 백필된 커밋 링크 → 온디맨드 시맨틱 조회로 단계적으로 확장한다.

  1. ticket   : 커밋 티켓(PAY-2041) == document_links(ticket)  — 확정(confidence=None)
  2. backfill : blamed 커밋에 사전 생성된 document_links(commit/file) — 백필 점수
  3. semantic : 커밋 메시지로 즉석 KB 조회 → 추정(낮은 확신)

각 결과에 matchType 과 confidence 를 실어 UI 가 신뢰도를 구분 표시한다.

👤 담당: 개발자 C
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import git
from app.core.knowledge_base import retrieve_passages
from app.core.tickets import extract_ticket
from app.db.models import Commit, Document, DocumentLink, Repository
from app.features.blame.service import extract_keywords


async def trace(db: AsyncSession, repo_path: str, file_path: str, line: int) -> list[dict]:
    """라인을 기준으로 연관 기획 문서 목록(다운로드 URL·신뢰도 포함)을 반환한다."""
    try:
        info = git.get_blame_info(repo_path, file_path, line)
        branch = git.get_current_branch(repo_path)
    except Exception:
        return []

    # 1. 티켓 정확 매칭
    ticket = extract_ticket(info.message, branch)
    if ticket:
        results = await _by_ticket(db, ticket)
        if results:
            return results

    # 2. 사전 백필된 커밋/파일 링크
    results = await _by_backfill(db, repo_path, info.commit_hash)
    if results:
        return results

    # 3. 온디맨드 시맨틱 폴백 (추정)
    # TODO(검증): 시맨틱 폴백은 매 조회마다 KB 를 호출한다. 호출 빈도/지연이 부담되면
    #   blame_explanations 처럼 (file_id, commit_id) 키로 결과를 캐시하는 방안 검토.
    return await _by_semantic(db, info.message)


async def _by_ticket(db: AsyncSession, ticket: str) -> list[dict]:
    stmt = (
        select(Document, DocumentLink)
        .join(DocumentLink, DocumentLink.document_id == Document.id)
        .where(DocumentLink.link_type == "ticket", DocumentLink.ticket == ticket)
    )
    rows = (await db.execute(stmt)).all()
    return [_match(doc, link, "ticket", confidence=None) for doc, link in rows]


async def _by_backfill(db: AsyncSession, repo_path: str, commit_hash: str) -> list[dict]:
    """blamed 커밋에 사전 생성된 document_links(commit/file)를 읽는다.

    레포/커밋이 백본에 없으면(백필 미실행) 빈 결과. 읽기 전용이라 레포를 새로 만들지 않는다.
    """
    repo = (
        await db.execute(select(Repository).where(Repository.identifier == repo_path))
    ).scalar_one_or_none()
    if repo is None:
        return []

    commit = (
        await db.execute(
            select(Commit).where(Commit.repo_id == repo.id, Commit.commit_hash == commit_hash)
        )
    ).scalar_one_or_none()
    if commit is None:
        return []

    stmt = (
        select(Document, DocumentLink)
        .join(DocumentLink, DocumentLink.document_id == Document.id)
        .where(
            DocumentLink.commit_id == commit.id,
            DocumentLink.link_type.in_(["commit", "file"]),
        )
        .order_by(DocumentLink.confidence.desc().nullslast())
    )
    rows = (await db.execute(stmt)).all()
    return [_match(doc, link, "backfill", confidence=link.confidence) for doc, link in rows]


async def _by_semantic(db: AsyncSession, commit_message: str) -> list[dict]:
    """커밋 메시지 키워드로 KB 를 즉석 조회해 추정 문서를 만든다(사전 링크가 없을 때)."""
    # TODO(검증): KB score 가 낮은 추정 결과까지 그대로 노출할지, 최소 점수로 거를지 결정.
    #   너무 약한 매칭을 "추정"으로 보여주면 오히려 신뢰를 떨어뜨릴 수 있다(실데이터로 판단).
    query = " ".join(extract_keywords(commit_message))
    if not query.strip():
        return []

    results: list[dict] = []
    seen: set[int] = set()
    for p in retrieve_passages(query):
        if p.document_id is None or p.document_id in seen:
            continue
        doc = await db.get(Document, p.document_id)
        if doc is None:
            continue
        seen.add(p.document_id)
        results.append(
            {
                "documentId": doc.id,
                "name": doc.original_name,
                "page": p.page,
                "excerpt": p.text[:500] if p.text else None,
                "downloadUrl": f"/api/documents/{doc.id}/download",
                "matchType": "semantic",
                "confidence": p.score,
            }
        )
    return results


def _match(doc: Document, link: DocumentLink, match_type: str, *, confidence: float | None) -> dict:
    return {
        "documentId": doc.id,
        "name": doc.original_name,
        "page": link.page,
        "excerpt": link.excerpt,
        "downloadUrl": f"/api/documents/{doc.id}/download",
        "matchType": match_type,
        "confidence": confidence,
    }
