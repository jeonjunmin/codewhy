"""commit_titles: '라인 수정 이력' 행 타이틀 캐시 (commit_hash 키)

Revision ID: 0008_commit_titles
Revises: 0007_blame_line_scope
Create Date: 2026-06-28

타이틀은 (커밋 메시지 + 그 라인 diff)에서 다듬은 결과로 commit_hash 에 종속·안정적이다.
인메모리 캐시 대신 DB 에 영속해, 재방문·서버 재시작 시 LLM(Bedrock) 재호출 없이 즉시 응답한다.
git sha 는 전역 유일이라 repo 결합 없이 commit_hash 단일 PK 로 둔다.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0008_commit_titles"
down_revision: Union[str, None] = "0007_blame_line_scope"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "commit_titles",
        sa.Column("commit_hash", sa.String(length=40), primary_key=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("commit_titles")
