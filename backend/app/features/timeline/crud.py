"""Timeline — PostgreSQL CRUD.

② 흐름: 확장이 보낸 커밋을 RDS에 저장하고, 저장된 이력을 LangGraph에 넘긴다.

  upsert_commits — 신규 커밋만 INSERT (기존 hash는 건너뜀)
  get_commits    — repo_path + file_path 기준 전체 이력 최신순 반환
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import CommitLog
from app.db.postgres import AsyncSessionLocal


async def upsert_commits(
    repo_path: str, file_path: str, commits: list[dict]
) -> None:
    if not commits:
        return

    async with AsyncSessionLocal() as db:
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
    repo_path: str, file_path: str, limit: int = 200
) -> list[dict]:
    """저장된 커밋 이력을 최신순으로 반환한다.

    graph.py 가 기대하는 형식:
      [{"hash": str, "author": str, "date": str, "subject": str}, ...]
    """
    async with AsyncSessionLocal() as db:
        rows = (await db.scalars(
            select(CommitLog)
            .where(
                CommitLog.repo_path == repo_path,
                CommitLog.file_path == file_path,
            )
            .order_by(CommitLog.date.desc())
            .limit(limit)
        )).all()

    return [
        {"hash": r.commit_hash, "author": r.author, "date": r.date, "subject": r.message}
        for r in rows
    ]
