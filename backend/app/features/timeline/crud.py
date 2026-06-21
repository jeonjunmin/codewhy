"""Timeline — PostgreSQL CRUD.

공유 백본(commits/files/commit_files)에 커밋 이력을 upsert 하고, 파일별 이력을 join 으로 읽는다.
타임라인 AI 요약 결과는 timeline_summaries 에 캐시한다(commit_set_hash 키).

👤 담당: 개발자 B
"""

import logging
from datetime import date, datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tickets import extract_ticket
from app.db import crud_common
from app.db.models import Commit, CommitFile, File, Repository, TimelineSummary

logger = logging.getLogger(__name__)


def _parse_date(value: str) -> date | None:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


async def upsert_commits(
    db: AsyncSession, repo_path: str, file_path: str, commits: list[dict]
) -> File:
    """커밋 목록을 공유 백본에 저장하고, 해당 File 행을 돌려준다.

    commits 형식: [{"hash","author","date","subject"}, ...] (확장이 로컬 git log 로 수집)
    """
    repo = await crud_common.get_or_create_repository(db, repo_path)
    logger.info("[crud] repo — id=%d  identifier=%r", repo.id, repo.identifier)

    file = await crud_common.get_or_create_file(db, repo.id, file_path)
    logger.info("[crud] file — id=%d  repo_id=%d  path=%s", file.id, file.repo_id, file_path)

    if commits:
        commit_values = [
            {
                "commit_hash": c["hash"],
                "author": c.get("author"),
                "committed_date": _parse_date(c.get("date", "")),
                "message": c.get("subject"),
                "ticket": extract_ticket(c.get("subject", "")),
            }
            for c in commits
        ]
        hash_to_id = await crud_common.upsert_commits_bulk(db, repo.id, commit_values)
        await crud_common.link_commits_files_bulk(db, list(hash_to_id.values()), file.id)

    await db.commit()
    return file


async def get_commits(db: AsyncSession, file_id: int, limit: int = 200) -> list[dict]:
    """파일의 커밋 이력을 최신순으로 반환한다.

    graph.py 가 기대하는 형식: [{"hash","author","date","subject"}, ...]
    """
    stmt = (
        select(Commit)
        .join(CommitFile, CommitFile.commit_id == Commit.id)
        .where(CommitFile.file_id == file_id)
        .order_by(Commit.committed_date.desc(), Commit.id.desc())
        .limit(limit)
    )
    rows = (await db.execute(stmt)).scalars().all()
    return [
        {
            "hash": c.commit_hash,
            "author": c.author or "",
            "date": c.committed_date.isoformat() if c.committed_date else "",
            "subject": c.message or "",
        }
        for c in rows
    ]


# ── 타임라인 요약 캐시 ──────────────────────────────────────────────────────────

async def get_cached_summary(
    db: AsyncSession, file_id: int, commit_set_hash: str
) -> TimelineSummary | None:
    stmt = select(TimelineSummary).where(
        TimelineSummary.file_id == file_id,
        TimelineSummary.commit_set_hash == commit_set_hash,
    )
    row = (await db.execute(stmt)).scalar_one_or_none()
    logger.info("[crud] get_cached_summary — file_id=%d  hash=%s…  hit=%s",
                file_id, commit_set_hash[:16], row is not None)

    # 동일 file_id 의 모든 기존 요약 해시 출력 (불일치 디버그용) — DEBUG 레벨에서만 추가 쿼리 실행
    if row is None and logger.isEnabledFor(logging.DEBUG):
        all_rows = (await db.execute(
            select(TimelineSummary.commit_set_hash).where(TimelineSummary.file_id == file_id)
        )).scalars().all()
        logger.debug("[crud] DB 내 file_id=%d 요약 해시 목록: %s",
                     file_id, [h[:16] + "…" for h in all_rows] if all_rows else "없음")

    return row


async def clear_summaries_for_file(
    db: AsyncSession, repo_path: str, file_path: str
) -> int:
    """현재 파일의 타임라인 요약 캐시를 모두 삭제하고, 삭제된 행 수를 반환한다.

    레포/파일 행은 '조회만' 한다(get_or_create 와 달리 없는 행을 만들지 않는다) —
    캐시 비우기가 의도치 않게 백본에 빈 파일 행을 남기지 않게 하기 위함이다.
    저장 때와 동일한 키로 지워야 하므로 file_path 는 라우터에서 상대경로로 정규화된 값을 받는다.
    """
    repo = (
        await db.execute(select(Repository).where(Repository.identifier == repo_path))
    ).scalar_one_or_none()
    if repo is None:
        return 0

    file = (
        await db.execute(
            select(File).where(File.repo_id == repo.id, File.file_path == file_path)
        )
    ).scalar_one_or_none()
    if file is None:
        return 0

    result = await db.execute(
        delete(TimelineSummary).where(TimelineSummary.file_id == file.id)
    )
    await db.commit()
    deleted = result.rowcount or 0
    logger.info("[crud] clear_summaries_for_file — file_id=%d 삭제=%d건", file.id, deleted)
    return deleted


async def save_summary(
    db: AsyncSession, file_id: int, commit_set_hash: str, result: dict
) -> None:
    """요약 결과를 upsert 한다. 같은 (file_id, commit_set_hash) 면 갱신."""
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    stmt = (
        pg_insert(TimelineSummary)
        .values(
            file_id=file_id,
            commit_set_hash=commit_set_hash,
            summary=result.get("summary", ""),
            milestones=result.get("milestones", []),
        )
        .on_conflict_do_update(
            index_elements=["file_id", "commit_set_hash"],
            set_={
                "summary": result.get("summary", ""),
                "milestones": result.get("milestones", []),
            },
        )
    )
    await db.execute(stmt)
    await db.commit()
