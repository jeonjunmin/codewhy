"""commit_issues: 커밋↔이슈 연결 영구 캐시 + commits.issues_indexed_at

Revision ID: 0006_commit_issues
Revises: 0005_merge_heads
Create Date: 2026-06-20

파일 단위 역추적의 API 호출을 줄이기 위한 2계층 캐시 중 1차(불변 연결) 캐시.
커밋↔이슈 연결은 git 히스토리상 바뀌지 않으므로 commit_issues 에 영구 저장하고,
commits.issues_indexed_at 으로 "이 커밋은 연결 추출을 끝냈다"를 표시해(0건이어도)
재조회를 막는다. cache-aside 증분 — 미인덱싱 커밋만 GitHub 에 조회한다.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0006_commit_issues"
down_revision: Union[str, None] = "0005_merge_heads"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "commits",
        sa.Column("issues_indexed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "commit_issues",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "commit_id",
            sa.BigInteger(),
            sa.ForeignKey("commits.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("issue_number", sa.Integer(), nullable=False),
        sa.Column("link_source", sa.String(length=16), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("commit_id", "issue_number", name="uq_commit_issues"),
    )
    op.create_index("ix_commit_issues_commit", "commit_issues", ["commit_id"])


def downgrade() -> None:
    op.drop_index("ix_commit_issues_commit", table_name="commit_issues")
    op.drop_table("commit_issues")
    op.drop_column("commits", "issues_indexed_at")
