"""ORM 모델.

commit_logs 테이블: 확장이 전달한 파일별 커밋 이력을 저장한다.
(repo_path, file_path, commit_hash) 복합 유니크로 중복 삽입을 방지한다.
"""

from datetime import datetime

from sqlalchemy import DateTime, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.postgres import Base


class CommitLog(Base):
    __tablename__ = "commit_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    repo_path: Mapped[str] = mapped_column(String(512), nullable=False)
    file_path: Mapped[str] = mapped_column(String(512), nullable=False)
    commit_hash: Mapped[str] = mapped_column(String(40), nullable=False)
    author: Mapped[str] = mapped_column(String(255), nullable=False)
    date: Mapped[str] = mapped_column(String(20), nullable=False)   # YYYY-MM-DD
    message: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("repo_path", "file_path", "commit_hash", name="uq_commit_log"),
    )
