"""blame_explanations: issue_url / attachments 컬럼 추가

Revision ID: 0004_blame_issue_attachments
Revises: 0003_blame_commit_grain
Create Date: 2026-06-06

컨텍스트 블레임의 요구사항 문서 출처를 Bedrock KB → GitHub Issue 첨부 링크로 전환.
캐시 적중 시에도 사이드바가 '출처 클릭→이슈 페이지', '첨부 카드 클릭→문서 다운로드' 를
재현할 수 있도록 issue_url(Text) + attachments(JSONB) 두 컬럼을 추가한다.

기존 KB 시절 캐시 행은 새 출처 의미와 다르므로 한 번 비운다(다음 조회 때 이슈 기반으로 재생성).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004_blame_issue_attachments"
down_revision: Union[str, None] = "0003_blame_commit_grain"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "blame_explanations",
        sa.Column("issue_url", sa.Text(), nullable=True),
    )
    op.add_column(
        "blame_explanations",
        sa.Column("attachments", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    # KB 출처로 채워졌던 기존 캐시는 의미가 달라졌으므로 비운다.
    op.execute("DELETE FROM blame_explanations")


def downgrade() -> None:
    op.drop_column("blame_explanations", "attachments")
    op.drop_column("blame_explanations", "issue_url")
