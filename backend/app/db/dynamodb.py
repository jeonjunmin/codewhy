"""캐시 헬퍼 — PostgreSQL 전환 버전.

기존 DynamoDB 방식에서 SQLAlchemy sync 세션으로 교체.
함수 시그니처는 동일하게 유지해 blame/traceability 라우터 변경 없음.
"""

from app.db.models import BlameCache, TimelineSummaryCache
from app.db.postgres import SyncSessionLocal


# ── blame_cache ───────────────────────────────────────────────────────────────

def get_blame_cache(repo_path: str, file_path: str, line: int) -> dict | None:
    file_line = f"{file_path}#{line}"
    with SyncSessionLocal() as db:
        row = db.query(BlameCache).filter_by(
            repo_path=repo_path, file_line=file_line
        ).first()
        return dict(row.data) if row else None


def put_blame_cache(repo_path: str, file_path: str, line: int, item: dict) -> None:
    file_line = f"{file_path}#{line}"
    with SyncSessionLocal() as db:
        row = db.query(BlameCache).filter_by(
            repo_path=repo_path, file_line=file_line
        ).first()
        if row:
            row.data = item
        else:
            db.add(BlameCache(repo_path=repo_path, file_line=file_line, data=item))
        db.commit()


# ── timeline_summary_cache ────────────────────────────────────────────────────

def get_timeline_cache(repo_path: str, file_path: str) -> dict | None:
    with SyncSessionLocal() as db:
        row = db.query(TimelineSummaryCache).filter_by(
            repo_path=repo_path, file_path=file_path
        ).first()
        return dict(row.data) if row else None


def put_timeline_cache(repo_path: str, file_path: str, item: dict) -> None:
    with SyncSessionLocal() as db:
        row = db.query(TimelineSummaryCache).filter_by(
            repo_path=repo_path, file_path=file_path
        ).first()
        if row:
            row.data = item
        else:
            db.add(TimelineSummaryCache(repo_path=repo_path, file_path=file_path, data=item))
        db.commit()
