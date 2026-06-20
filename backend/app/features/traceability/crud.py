"""Requirement Trace — 커밋↔이슈 연결(commit_issues) 캐시 CRUD.

파일 단위 역추적의 1차 캐시. 커밋↔이슈 연결은 불변이므로 한 번 추출해 영구 저장하고,
commits.issues_indexed_at 으로 인덱싱 완료를 표시해(0건이어도) 재조회를 막는다.
이슈의 가변 메타는 여기 두지 않는다 — 조회 시점에 번호 집합으로 일괄 refresh.

👤 담당: 개발자 C
"""

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Commit, CommitFile, CommitIssue


async def get_uncached_commit_ids(db: AsyncSession, commit_ids: list[int]) -> list[int]:
    """주어진 커밋 중 아직 이슈 인덱싱을 안 한 것(issues_indexed_at IS NULL)만 반환한다."""
    if not commit_ids:
        return []
    stmt = select(Commit.id).where(
        Commit.id.in_(commit_ids),
        Commit.issues_indexed_at.is_(None),
    )
    return list((await db.execute(stmt)).scalars().all())


async def save_commit_issues(
    db: AsyncSession, commit_id: int, links: list[dict]
) -> None:
    """한 커밋의 연관 이슈 연결을 저장하고 인덱싱 완료를 표시한다.

    links: [{"issue_number","link_source","confidence"}, ...] (비어 있을 수 있음).
    연결이 0건이어도 issues_indexed_at 을 찍어 다음에 재조회하지 않게 한다(불변 캐시).
    """
    if links:
        values = [
            {
                "commit_id": commit_id,
                "issue_number": link["issue_number"],
                "link_source": link["link_source"],
                "confidence": link.get("confidence"),
            }
            for link in links
        ]
        stmt = (
            pg_insert(CommitIssue)
            .values(values)
            .on_conflict_do_nothing(index_elements=["commit_id", "issue_number"])
        )
        await db.execute(stmt)

    await db.execute(
        Commit.__table__.update()
        .where(Commit.id == commit_id)
        .values(issues_indexed_at=func.now())
    )


async def get_issue_links_for_file(
    db: AsyncSession, file_id: int
) -> list[tuple[int, str, float | None]]:
    """이 파일을 건드린 모든 커밋의 연관 이슈 연결을 모아 반환한다.

    같은 이슈가 여러 커밋에서 다른 경로(link_source)로 잡힐 수 있으므로 dedup 하지 않고
    전부 돌려준다 — 호출 측(service)이 신뢰도 순위로 합친다.
    반환: [(issue_number, link_source, confidence), ...].
    """
    stmt = (
        select(CommitIssue.issue_number, CommitIssue.link_source, CommitIssue.confidence)
        .join(CommitFile, CommitFile.commit_id == CommitIssue.commit_id)
        .where(CommitFile.file_id == file_id)
    )
    return [tuple(row) for row in (await db.execute(stmt)).all()]
