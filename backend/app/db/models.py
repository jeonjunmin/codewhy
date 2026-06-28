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
    String,
    Text,
    UniqueConstraint,
    func,
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
    # 이 커밋의 연관 이슈(commit_issues) 추출을 시도한 시각. NULL = 아직 인덱싱 안 함.
    # 커밋↔이슈 연결은 불변이므로, 한 번 채워지면 재조회하지 않는다(0건이어도 타임스탬프로 표시).
    issues_indexed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
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

    UNIQUE(file_id, commit_id, line_history_hash) — 두 스코프를 한 테이블에 담는다:
      · line_history_hash='' → 커밋×파일 스코프. "왜 바뀌었나"는 줄이 아니라 커밋이 그 파일에
        가한 변경의 속성이므로, 같은 커밋이 바꾼 여러 줄(단일 리비전)은 설명 1개를 공유한다.
      · line_history_hash=<해시> → 라인 스코프. 여러 번 수정된 줄(멀티 리비전)은 '이력 반영'
        설명이 줄마다 달라야 하므로, 그 줄의 이력 해시로 분리 저장한다(같은 커밋의 다른 줄과 충돌 X).
    라인이 밀려 blamed 커밋이 달라지거나 줄 이력이 바뀌면 키가 달라져 자동 미스 → 재계산(stale 방지).
    """

    __tablename__ = "blame_explanations"
    __table_args__ = (
        UniqueConstraint("file_id", "commit_id", "line_history_hash", name="uq_blame_file_commit_line"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    file_id: Mapped[int] = mapped_column(ForeignKey("files.id", ondelete="CASCADE"), nullable=False)
    commit_id: Mapped[int] = mapped_column(ForeignKey("commits.id", ondelete="CASCADE"), nullable=False)
    # 라인 스코프 캐시 키. '' = 커밋 스코프(단일 리비전, 줄들이 설명 공유).
    # 멀티 리비전 줄은 그 줄의 이력 해시를 담아 같은 커밋의 다른 줄과 분리한다.
    line_history_hash: Mapped[str] = mapped_column(String(64), nullable=False, server_default="")
    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    ai_suggestion: Mapped[str | None] = mapped_column(Text)
    source_ref: Mapped[str | None] = mapped_column(Text)             # 예: "Issue #12: 결제 취소 정책 변경"
    issue_url: Mapped[str | None] = mapped_column(Text)              # 사이드바 '출처' 클릭 시 외부 링크
    attachments: Mapped[list | None] = mapped_column(JSONB)          # [{label, url}, ...]
    change_stats: Mapped[dict | None] = mapped_column(JSONB)         # {added, removed}
    pr_info: Mapped[dict | None] = mapped_column(JSONB)              # {url, lines}
    related_changes: Mapped[list | None] = mapped_column(JSONB)      # RelatedChange[]
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


class CommitIssue(Base):
    """커밋↔GitHub Issue 연결(불변) 영구 캐시 — 파일 단위 역추적의 1차 캐시.

    "이 커밋이 어떤 이슈를 참조하는가"는 git 히스토리상 바뀌지 않는 사실이므로 영구 저장한다.
    파일을 열 때 미인덱싱 커밋(commits.issues_indexed_at IS NULL)만 GitHub 에 조회해 채우는
    cache-aside 증분 방식이라, 새 커밋(=새 이슈 참조)은 다음 조회에 자동 반영된다.

    이슈의 '가변 메타'(state/labels/commentCount 등)는 여기 두지 않는다 — 그건 조회 시점에
    이슈 번호 집합으로 일괄 refresh 해 항상 최신을 유지한다(신선도 보장).

    link_source: issue(PR 본문 직결) | ticket(티켓 검색) | semantic(키워드 검색).
    """

    __tablename__ = "commit_issues"
    __table_args__ = (
        UniqueConstraint("commit_id", "issue_number", name="uq_commit_issues"),
        Index("ix_commit_issues_commit", "commit_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    commit_id: Mapped[int] = mapped_column(ForeignKey("commits.id", ondelete="CASCADE"), nullable=False)
    issue_number: Mapped[int] = mapped_column(Integer, nullable=False)
    link_source: Mapped[str] = mapped_column(String(16), nullable=False)
    confidence: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    commit: Mapped["Commit"] = relationship()


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


class CommitTitle(Base):
    """'라인 수정 이력' 행 타이틀 캐시 — commit_hash 로만 키잉한다.

    타이틀은 (커밋 메시지 + 그 라인 diff)에서 다듬은 결과로 commit_hash 에 종속·안정적이다.
    git sha 는 전역 유일이라 repo 결합이 필요 없다. blame 본문(blame_explanations)처럼
    DB 에 영속해, 재방문·서버 재시작 시 LLM 재호출 없이 즉시 응답하게 한다.
    """

    __tablename__ = "commit_titles"

    commit_hash: Mapped[str] = mapped_column(String(40), primary_key=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

