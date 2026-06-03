"""PostgreSQL ORM 모델.

테이블 3개:
  commit_logs           — Timeline 커밋 이력 저장
  blame_cache           — Context Blame AI 결과 캐시
  timeline_summary_cache — Timeline AI 요약 캐시
"""

from datetime import datetime

from sqlalchemy import JSON, DateTime, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.postgres import Base


class CommitLog(Base):
    """파일별 커밋 이력. (repo_path, file_path, commit_hash) 복합 UNIQUE."""
    __tablename__ = "commit_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    repo_path: Mapped[str] = mapped_column(String(512), nullable=False)
    file_path: Mapped[str] = mapped_column(String(512), nullable=False)
    commit_hash: Mapped[str] = mapped_column(String(40), nullable=False)
    author: Mapped[str] = mapped_column(String(255), nullable=False)
    date: Mapped[str] = mapped_column(String(10), nullable=False)   # YYYY-MM-DD
    message: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("repo_path", "file_path", "commit_hash", name="uq_commit_log"),
    )


class BlameCache(Base):
    """Context Blame 결과 캐시. (repo_path, file_line) 복합 UNIQUE."""
    __tablename__ = "blame_cache"

    id: Mapped[int] = mapped_column(primary_key=True)
    repo_path: Mapped[str] = mapped_column(String(512), nullable=False)
    file_line: Mapped[str] = mapped_column(String(512), nullable=False)  # "{file_path}#{line}"
    data: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("repo_path", "file_line", name="uq_blame_cache"),
    )


class TimelineSummaryCache(Base):
    """Timeline AI 요약 캐시. (repo_path, file_path) 복합 UNIQUE."""
    __tablename__ = "timeline_summary_cache"

    id: Mapped[int] = mapped_column(primary_key=True)
    repo_path: Mapped[str] = mapped_column(String(512), nullable=False)
    file_path: Mapped[str] = mapped_column(String(512), nullable=False)
    data: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("repo_path", "file_path", name="uq_timeline_summary_cache"),
    )
