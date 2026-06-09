"""공유 백본(repositories/commits/files/commit_files) CRUD 헬퍼.

세 기능이 모두 "이 레포의 이 파일의 이 커밋" 을 행으로 확보해야 하므로, 그 get-or-create/upsert
로직을 한곳에 모아 재사용한다. PostgreSQL ON CONFLICT 로 동시 요청에도 중복 없이 안전하게 upsert 한다.
"""

from datetime import date

from sqlalchemy import func, select
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


async def upsert_commits_bulk(
    db: AsyncSession, repo_id: int, commits: list[dict]
) -> dict[str, int]:
    """커밋 목록을 한 번의 SQL로 upsert 하고 commit_hash → commit_id 매핑을 반환한다.

    각 dict 는 {"commit_hash","author","committed_date","message","ticket"} 형식
    (이미 파싱된 값) — 호출 측이 정규화를 책임진다.
    빈 값으로 기존 메타데이터를 덮지 않도록 COALESCE 로 보존한다 (upsert_commit 과 동일 정책).
    """
    if not commits:
        return {}

    values = [{**c, "repo_id": repo_id} for c in commits]
    stmt = pg_insert(Commit).values(values)
    stmt = stmt.on_conflict_do_update(
        index_elements=["repo_id", "commit_hash"],
        set_={
            "author":         func.coalesce(stmt.excluded.author, Commit.author),
            "message":        func.coalesce(stmt.excluded.message, Commit.message),
            "ticket":         func.coalesce(stmt.excluded.ticket, Commit.ticket),
            "committed_date": func.coalesce(stmt.excluded.committed_date, Commit.committed_date),
        },
    ).returning(Commit.commit_hash, Commit.id)

    rows = (await db.execute(stmt)).all()
    return {row.commit_hash: row.id for row in rows}


async def link_commits_files_bulk(
    db: AsyncSession, commit_ids: list[int], file_id: int
) -> None:
    """commit_files 링크를 한 번의 SQL로 다중 INSERT 한다 (이미 존재하면 무시)."""
    if not commit_ids:
        return

    values = [{"commit_id": cid, "file_id": file_id} for cid in commit_ids]
    stmt = (
        pg_insert(CommitFile)
        .values(values)
        .on_conflict_do_nothing(index_elements=["commit_id", "file_id"])
    )
    await db.execute(stmt)


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
