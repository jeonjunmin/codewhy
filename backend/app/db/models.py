"""CodeWhy 통합 스키마 — SQLAlchemy ORM 모델.

세 기능이 공유하는 commit/file 백본을 중심으로 정규화했다. 작성자·날짜·커밋 메시지·티켓 같은
"세 기능 모두에서 보여줘야 하는" 데이터는 commits/files 에 한 번만 저장하고, 기능별 산출물
(블레임 설명/타임라인 요약/문서 매칭)은 백본을 FK 로 참조한다.

    repositories ─┬─ commits ─┬─ commit_files ─ files
                  │           │
      blame_explanations ─────┘           timeline_summaries
      documents ─ document_links ── (ticket | commit_id | file_id)

스키마 변경은 반드시 Alembic 마이그레이션(autogenerate)으로 반영한다.
"""

from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.postgres import Base


# ── 공유 백본 ──────────────────────────────────────────────────────────────────

class Repository(Base):
    """레포 식별자 — repo_path/remote URL 문자열을 반복 저장하지 않기 위한 루트."""

    __tablename__ = "repositories"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    identifier: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    name: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    commits: Mapped[list["Commit"]] = relationship(back_populates="repository")
    files: Mapped[list["File"]] = relationship(back_populates="repository")


class Commit(Base):
    """git 커밋 — 블레임·타임라인이 공통으로 참조하는 핵심 엔티티."""

    __tablename__ = "commits"
    __table_args__ = (
        UniqueConstraint("repo_id", "commit_hash", name="uq_commits_repo_hash"),
        Index("ix_commits_ticket", "ticket"),
        Index("ix_commits_author_email", "author_email"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    repo_id: Mapped[int] = mapped_column(ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False)
    commit_hash: Mapped[str] = mapped_column(String(40), nullable=False)
    author: Mapped[str | None] = mapped_column(Text)
    author_email: Mapped[str | None] = mapped_column(Text)
    committed_date: Mapped[date | None] = mapped_column(Date)
    message: Mapped[str | None] = mapped_column(Text)
    ticket: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    repository: Mapped["Repository"] = relationship(back_populates="commits")
    file_changes: Mapped[list["CommitFile"]] = relationship(back_populates="commit")


class File(Base):
    """레포 내 파일 경로."""

    __tablename__ = "files"
    __table_args__ = (
        UniqueConstraint("repo_id", "file_path", name="uq_files_repo_path"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    repo_id: Mapped[int] = mapped_column(ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False)
    file_path: Mapped[str] = mapped_column(Text, nullable=False)

    repository: Mapped["Repository"] = relationship(back_populates="files")
    changes: Mapped[list["CommitFile"]] = relationship(back_populates="file")


class CommitFile(Base):
    """커밋↔파일 N:M + 변경량. "이 파일의 모든 커밋"을 join 으로 조회한다."""

    __tablename__ = "commit_files"

    commit_id: Mapped[int] = mapped_column(ForeignKey("commits.id", ondelete="CASCADE"), primary_key=True)
    file_id: Mapped[int] = mapped_column(ForeignKey("files.id", ondelete="CASCADE"), primary_key=True)
    lines_added: Mapped[int] = mapped_column(Integer, default=0)
    lines_removed: Mapped[int] = mapped_column(Integer, default=0)

    commit: Mapped["Commit"] = relationship(back_populates="file_changes")
    file: Mapped["File"] = relationship(back_populates="changes")


# ── 기능별 테이블 ──────────────────────────────────────────────────────────────

class BlameExplanation(Base):
    """컨텍스트 블레임 AI 결과 캐시.

    UNIQUE(file_id, commit_id) — "왜 바뀌었나"는 줄(line)이 아니라 커밋이 그 파일에 가한
    변경의 속성이다. 줄은 그 커밋을 찾기 위한 포인터(git blame)일 뿐이므로, 같은 커밋이 바꾼
    여러 줄은 설명 1개를 공유한다(커밋×파일 단위 dedup). 라인이 밀려 blamed 커밋이 달라지면
    (file_id, commit_id) 가 달라져 자동 캐시 미스 → 재계산되어 stale 응답을 막는다.
    """

    __tablename__ = "blame_explanations"
    __table_args__ = (
        UniqueConstraint("file_id", "commit_id", name="uq_blame_file_commit"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    file_id: Mapped[int] = mapped_column(ForeignKey("files.id", ondelete="CASCADE"), nullable=False)
    commit_id: Mapped[int] = mapped_column(ForeignKey("commits.id", ondelete="CASCADE"), nullable=False)
    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    ai_suggestion: Mapped[str | None] = mapped_column(Text)
    source_ref: Mapped[str | None] = mapped_column(Text)
    change_stats: Mapped[dict | None] = mapped_column(JSONB)
    pr_info: Mapped[dict | None] = mapped_column(JSONB)
    related_changes: Mapped[list | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    commit: Mapped["Commit"] = relationship()


class TimelineSummary(Base):
    """타임라인 AI 요약 캐시.

    UNIQUE(file_id, commit_set_hash) — commit_set_hash 는 파일의 정렬된 커밋 해시 목록의 SHA-256.
    커밋 집합이 그대로면 적중, 새 커밋이 생기면 해시가 달라져 재요약(LangGraph/Bedrock 재실행).
    """

    __tablename__ = "timeline_summaries"
    __table_args__ = (
        UniqueConstraint("file_id", "commit_set_hash", name="uq_timeline_file_sethash"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    file_id: Mapped[int] = mapped_column(ForeignKey("files.id", ondelete="CASCADE"), nullable=False)
    commit_set_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    milestones: Mapped[list | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TimelineSummaryCache(Base):
    """프로젝트 파일별 마지막 분석 커밋 해시 캐시. (repo_path, file_path) UNIQUE."""

    __tablename__ = "timeline_summary_cache"
    __table_args__ = (
        UniqueConstraint("repo_path", "file_path", name="uq_timeline_summary_cache"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    repo_path: Mapped[str] = mapped_column(String(512), nullable=False)
    file_path: Mapped[str] = mapped_column(String(512), nullable=False)
    data: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


# ── 역추적: 서버 문서 저장 + git 연결 ──────────────────────────────────────────

class Document(Base):
    """서버에 업로드된 기획 문서 메타데이터. 바이너리는 storage_key 위치(디스크/S3)에 저장."""

    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    repo_id: Mapped[int | None] = mapped_column(ForeignKey("repositories.id", ondelete="SET NULL"))
    original_name: Mapped[str] = mapped_column(Text, nullable=False)
    storage_key: Mapped[str] = mapped_column(Text, nullable=False)
    content_type: Mapped[str | None] = mapped_column(Text)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    page_count: Mapped[int | None] = mapped_column(Integer)
    uploaded_by: Mapped[str | None] = mapped_column(Text)
    file_data: Mapped[bytes | None] = mapped_column(LargeBinary)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    indexed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    links: Mapped[list["DocumentLink"]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )


class DocumentLink(Base):
    """문서(특정 페이지/구절)와 git 히스토리를 잇는 다리."""

    __tablename__ = "document_links"
    __table_args__ = (
        Index("ix_document_links_ticket", "ticket"),
        Index("ix_document_links_commit", "commit_id"),
        Index("ix_document_links_file", "file_id"),
        Index(
            "uq_doclinks_doc_commit_type",
            "document_id", "commit_id", "link_type",
            unique=True,
            postgresql_where=text("commit_id IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), nullable=False)
    link_type: Mapped[str] = mapped_column(String(16), nullable=False)
    ticket: Mapped[str | None] = mapped_column(Text)
    commit_id: Mapped[int | None] = mapped_column(ForeignKey("commits.id", ondelete="CASCADE"))
    file_id: Mapped[int | None] = mapped_column(ForeignKey("files.id", ondelete="CASCADE"))
    page: Mapped[int | None] = mapped_column(Integer)
    section: Mapped[str | None] = mapped_column(Text)
    excerpt: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    document: Mapped["Document"] = relationship(back_populates="links")
