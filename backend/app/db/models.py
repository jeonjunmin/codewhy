"""PostgreSQL ORM 모델.

테이블 6개:
  files                  — 소스 파일 레지스트리 (timeline_summaries.file_id FK 원본)
  commit_logs            — Timeline 커밋 이력 저장
  blame_cache            — Context Blame AI 결과 캐시
  timeline_summary_cache — Timeline AI 요약 캐시
  project_summaries      — 프로젝트 초기 분석 결과 (배경 캐싱)
  timeline_summaries     — 파일별 LangGraph 분석 결과 (기존 테이블 매핑)
"""

import enum
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, JSON, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.postgres import Base


# ── 레포지토리 레지스트리 ─────────────────────────────────────────────────────

class Repository(Base):
    """프로젝트 레포지토리 — 실제 DB 컬럼 구조에 맞게 매핑.

    identifier 로 조회/생성하며, 존재하지 않으면 자동 INSERT 한다.
    """
    __tablename__ = "repositories"
    __table_args__ = {"extend_existing": True}

    id:         Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    identifier: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    name:       Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


# ── 파일 레지스트리 ───────────────────────────────────────────────────────────

class File(Base):
    """소스 파일 레지스트리 — 실제 DB 컬럼 구조에 맞게 매핑.

    timeline_summaries.file_id 의 FK 원본.
    읽기 전용으로만 사용한다 (INSERT 는 외부 시스템에서 관리).
    """
    __tablename__ = "files"
    __table_args__ = {"extend_existing": True}

    id:        Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    repo_id:   Mapped[int] = mapped_column(BigInteger, nullable=False)          # 속한 레포 ID
    file_path: Mapped[str] = mapped_column(String(1024), nullable=False, index=True)


class CommitLog(Base):
    """파일별 커밋 이력. (repo_path, file_path, commit_hash) 복합 UNIQUE."""
    __tablename__ = "commit_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    repo_path: Mapped[str] = mapped_column(String(512), nullable=False)
    file_path: Mapped[str] = mapped_column(String(512), nullable=False)
    commit_hash: Mapped[str] = mapped_column(String(40), nullable=False)
    author: Mapped[str] = mapped_column(String(255), nullable=False)
    date: Mapped[str] = mapped_column(String(10), nullable=False)
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
    file_line: Mapped[str] = mapped_column(String(512), nullable=False)
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


# ── 프로젝트 초기 분석 ────────────────────────────────────────────────────────

class ProjectStatus(str, enum.Enum):
    PENDING    = "PENDING"
    PROCESSING = "PROCESSING"
    COMPLETED  = "COMPLETED"
    FAILED     = "FAILED"


class ProjectSummary(Base):
    """프로젝트 최초 로드 시 Bedrock이 생성한 전체 요약.

    SQLite / PostgreSQL 양쪽에서 동작한다.
    project_path 는 UNIQUE + 단독 조회 INDEX 를 모두 가진다.
    """
    __tablename__ = "project_summaries"

    id: Mapped[int] = mapped_column(primary_key=True)

    # 경로로 자주 단독 조회하므로 unique 외에 index 도 명시
    project_path: Mapped[str] = mapped_column(
        String(1024), unique=True, index=True, nullable=False
    )

    # 기본값 'PENDING' — PENDING / PROCESSING / COMPLETED / FAILED
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=ProjectStatus.PENDING
    )

    summary_text: Mapped[str | None] = mapped_column(Text, nullable=True)

    # 처음엔 없을 수 있음(None). 분석 성공 시 git HEAD 해시(40자) 저장
    last_commit_hash: Mapped[str | None] = mapped_column(String(40), nullable=True)

    # updated_at: ORM 레벨에서 자동 갱신 — SQLite·PostgreSQL 모두 호환
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )


# ── 파일별 타임라인 분석 ──────────────────────────────────────────────────────

class TimelineSummary(Base):
    """파일별 LangGraph 분석 결과.

    기존 DB 테이블 timeline_summaries 에 매핑 (이미 존재하는 테이블이므로 DDL 생성 안 함).
    milestones 컬럼은 JSONB 타입으로 커밋 메타데이터를 저장한다.
    """
    __tablename__ = "timeline_summaries"
    __table_args__ = {"extend_existing": True}  # 이미 존재하는 테이블 — create_all 무시

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    # 파일 식별자 — 호출 측에서 hash(file_path) 로 생성한 정수
    file_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)

    # 분석 당시 최신 git 커밋 해시 (중복 분석 방지용)
    commit_set_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    # Bedrock 이 생성한 파일 요약
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)

    # 커밋 메타데이터 JSON {"type": "feat", "domain": "auth", ...}
    milestones: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

