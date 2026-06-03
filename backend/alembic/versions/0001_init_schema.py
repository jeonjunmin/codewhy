"""init unified schema (repositories/commits/files + feature tables + documents)

Revision ID: 0001_init_schema
Revises:
Create Date: 2026-06-03

세 기능(블레임/타임라인/역추적)이 공유하는 commit/file 백본과 기능별 캐시·문서 테이블을 생성한다.
모델 정의는 app/db/models.py 참고. 이후 스키마 변경은 autogenerate 마이그레이션으로 관리한다.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_init_schema"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── 공유 백본 ──────────────────────────────────────────────────────────────
    op.create_table(
        "repositories",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("identifier", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("identifier", name="uq_repositories_identifier"),
    )

    op.create_table(
        "commits",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("repo_id", sa.BigInteger(), sa.ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False),
        sa.Column("commit_hash", sa.String(length=40), nullable=False),
        sa.Column("author", sa.Text(), nullable=True),
        sa.Column("author_email", sa.Text(), nullable=True),
        sa.Column("committed_date", sa.Date(), nullable=True),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("ticket", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("repo_id", "commit_hash", name="uq_commits_repo_hash"),
    )
    op.create_index("ix_commits_ticket", "commits", ["ticket"])
    op.create_index("ix_commits_author_email", "commits", ["author_email"])

    op.create_table(
        "files",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("repo_id", sa.BigInteger(), sa.ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False),
        sa.Column("file_path", sa.Text(), nullable=False),
        sa.UniqueConstraint("repo_id", "file_path", name="uq_files_repo_path"),
    )

    op.create_table(
        "commit_files",
        sa.Column("commit_id", sa.BigInteger(), sa.ForeignKey("commits.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("file_id", sa.BigInteger(), sa.ForeignKey("files.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("lines_added", sa.Integer(), server_default="0"),
        sa.Column("lines_removed", sa.Integer(), server_default="0"),
    )
    op.create_index("ix_commit_files_file", "commit_files", ["file_id"])

    # ── 기능별 테이블 ──────────────────────────────────────────────────────────
    op.create_table(
        "blame_explanations",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("file_id", sa.BigInteger(), sa.ForeignKey("files.id", ondelete="CASCADE"), nullable=False),
        sa.Column("line_no", sa.Integer(), nullable=False),
        sa.Column("commit_id", sa.BigInteger(), sa.ForeignKey("commits.id", ondelete="CASCADE"), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=False),
        sa.Column("ai_suggestion", sa.Text(), nullable=True),
        sa.Column("source_ref", sa.Text(), nullable=True),
        sa.Column("change_stats", postgresql.JSONB(), nullable=True),
        sa.Column("pr_info", postgresql.JSONB(), nullable=True),
        sa.Column("related_changes", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("file_id", "line_no", "commit_id", name="uq_blame_file_line_commit"),
    )

    op.create_table(
        "timeline_summaries",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("file_id", sa.BigInteger(), sa.ForeignKey("files.id", ondelete="CASCADE"), nullable=False),
        sa.Column("commit_set_hash", sa.String(length=64), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("milestones", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("file_id", "commit_set_hash", name="uq_timeline_file_sethash"),
    )

    # ── 역추적: 문서 + git 연결 ─────────────────────────────────────────────────
    op.create_table(
        "documents",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("repo_id", sa.BigInteger(), sa.ForeignKey("repositories.id", ondelete="SET NULL"), nullable=True),
        sa.Column("original_name", sa.Text(), nullable=False),
        sa.Column("storage_key", sa.Text(), nullable=False),
        sa.Column("content_type", sa.Text(), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column("uploaded_by", sa.Text(), nullable=True),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "document_links",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("document_id", sa.BigInteger(), sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("link_type", sa.String(length=16), nullable=False),
        sa.Column("ticket", sa.Text(), nullable=True),
        sa.Column("commit_id", sa.BigInteger(), sa.ForeignKey("commits.id", ondelete="CASCADE"), nullable=True),
        sa.Column("file_id", sa.BigInteger(), sa.ForeignKey("files.id", ondelete="CASCADE"), nullable=True),
        sa.Column("page", sa.Integer(), nullable=True),
        sa.Column("section", sa.Text(), nullable=True),
        sa.Column("excerpt", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_document_links_ticket", "document_links", ["ticket"])
    op.create_index("ix_document_links_commit", "document_links", ["commit_id"])
    op.create_index("ix_document_links_file", "document_links", ["file_id"])


def downgrade() -> None:
    op.drop_table("document_links")
    op.drop_table("documents")
    op.drop_table("timeline_summaries")
    op.drop_table("blame_explanations")
    op.drop_index("ix_commit_files_file", table_name="commit_files")
    op.drop_table("commit_files")
    op.drop_table("files")
    op.drop_index("ix_commits_author_email", table_name="commits")
    op.drop_index("ix_commits_ticket", table_name="commits")
    op.drop_table("commits")
    op.drop_table("repositories")
