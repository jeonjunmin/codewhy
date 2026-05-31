"""Timeline — RDS CRUD.

② 흐름: EC2가 DB에서 커밋 이력을 읽어오는 레이어.
   - upsert_commits : 확장에서 받은 커밋 목록 중 신규만 INSERT
   - get_commits    : 저장된 전체 이력을 최신순으로 SELECT
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import CommitLog


async def upsert_commits(
    db: AsyncSession, repo_path: str, file_path: str, commits: list[dict]
) -> None:
    hashes = [c["hash"] for c in commits]

    existing = set(
        (await db.scalars(
            select(CommitLog.commit_hash).where(
                CommitLog.repo_path == repo_path,
                CommitLog.file_path == file_path,
                CommitLog.commit_hash.in_(hashes),
            )
        )).all()
    )

    new_rows = [
        CommitLog(
            repo_path=repo_path,
            file_path=file_path,
            commit_hash=c["hash"],
            author=c["author"],
            date=c["date"],
            message=c["subject"],
        )
        for c in commits
        if c["hash"] not in existing
    ]
    if new_rows:
        db.add_all(new_rows)
        await db.commit()


async def get_commits(
    db: AsyncSession, repo_path: str, file_path: str
) -> list[dict]:
    rows = (await db.scalars(
        select(CommitLog)
        .where(CommitLog.repo_path == repo_path, CommitLog.file_path == file_path)
        .order_by(CommitLog.date.desc())
    )).all()

    return [
        {"hash": r.commit_hash, "author": r.author, "date": r.date, "subject": r.message}
        for r in rows
    ]
