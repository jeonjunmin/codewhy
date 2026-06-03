"""공유 백본(repositories/commits/files/commit_files) CRUD 헬퍼.

세 기능이 모두 "이 레포의 이 파일의 이 커밋" 을 행으로 확보해야 하므로, 그 get-or-create/upsert
로직을 한곳에 모아 재사용한다. PostgreSQL ON CONFLICT 로 동시 요청에도 중복 없이 안전하게 upsert 한다.
"""

from datetime import date

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Commit, CommitFile, File, Repository


async def get_or_create_repository(db: AsyncSession, identifier: str) -> Repository:
    stmt = (
        pg_insert(Repository)
        .values(identifier=identifier)
        .on_conflict_do_nothing(index_elements=["identifier"])
    )
    await db.execute(stmt)
    repo = (
        await db.execute(select(Repository).where(Repository.identifier == identifier))
    ).scalar_one()
    return repo


async def get_or_create_file(db: AsyncSession, repo_id: int, file_path: str) -> File:
    stmt = (
        pg_insert(File)
        .values(repo_id=repo_id, file_path=file_path)
        .on_conflict_do_nothing(index_elements=["repo_id", "file_path"])
    )
    await db.execute(stmt)
    file = (
        await db.execute(
            select(File).where(File.repo_id == repo_id, File.file_path == file_path)
        )
    ).scalar_one()
    return file


async def upsert_commit(
    db: AsyncSession,
    repo_id: int,
    commit_hash: str,
    *,
    author: str | None = None,
    author_email: str | None = None,
    committed_date: date | None = None,
    message: str | None = None,
    ticket: str | None = None,
) -> Commit:
    """커밋을 upsert 한다. 같은 (repo_id, commit_hash) 면 메타데이터를 갱신한다."""
    values = {
        "repo_id": repo_id,
        "commit_hash": commit_hash,
        "author": author,
        "author_email": author_email,
        "committed_date": committed_date,
        "message": message,
        "ticket": ticket,
    }
    stmt = (
        pg_insert(Commit)
        .values(**values)
        .on_conflict_do_update(
            index_elements=["repo_id", "commit_hash"],
            # 빈 값으로 기존 메타데이터를 덮어쓰지 않도록 COALESCE 로 보존
            set_={
                "author": Commit.author if author is None else values["author"],
                "message": Commit.message if message is None else values["message"],
                "ticket": Commit.ticket if ticket is None else values["ticket"],
                "committed_date": (
                    Commit.committed_date if committed_date is None else values["committed_date"]
                ),
            },
        )
    )
    await db.execute(stmt)
    commit = (
        await db.execute(
            select(Commit).where(
                Commit.repo_id == repo_id, Commit.commit_hash == commit_hash
            )
        )
    ).scalar_one()
    return commit


async def link_commit_file(
    db: AsyncSession, commit_id: int, file_id: int, lines_added: int = 0, lines_removed: int = 0
) -> None:
    """commit_files 링크를 upsert 한다 (커밋이 이 파일을 건드렸음 + 변경량)."""
    stmt = (
        pg_insert(CommitFile)
        .values(
            commit_id=commit_id,
            file_id=file_id,
            lines_added=lines_added,
            lines_removed=lines_removed,
        )
        .on_conflict_do_update(
            index_elements=["commit_id", "file_id"],
            set_={"lines_added": lines_added, "lines_removed": lines_removed},
        )
    )
    await db.execute(stmt)
