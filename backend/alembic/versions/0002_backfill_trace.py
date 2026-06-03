"""backfill trace: document_links.confidence + documents.indexed_at + dedup index

Revision ID: 0002_backfill_trace
Revises: 0001_init_schema
Create Date: 2026-06-03

브라운필드(SM) 온보딩 지원:
 - document_links.confidence : 백필/시맨틱 매칭 점수(0~1). 티켓 정확매칭은 NULL(=확정).
 - documents.indexed_at      : 시맨틱 인덱스(KB) 적재 완료 시각. 미적재면 NULL.
 - uq_doclinks_doc_commit_type : (document_id, commit_id, link_type) 부분 유니크 인덱스로
   커밋 백필 재실행 시 중복 링크 생성을 막는다(commit_id 가 있는 행에만 적용).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002_backfill_trace"
down_revision: Union[str, None] = "0001_init_schema"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "document_links",
        sa.Column("confidence", sa.Float(), nullable=True),
    )
    op.add_column(
        "documents",
        sa.Column("indexed_at", sa.DateTime(timezone=True), nullable=True),
    )
    # 커밋 백필 dedup — commit_id 가 채워진 행에만 유니크 강제(티켓 링크는 commit_id NULL 이라 무관).
    op.create_index(
        "uq_doclinks_doc_commit_type",
        "document_links",
        ["document_id", "commit_id", "link_type"],
        unique=True,
        postgresql_where=sa.text("commit_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_doclinks_doc_commit_type", table_name="document_links")
    op.drop_column("documents", "indexed_at")
    op.drop_column("document_links", "confidence")
