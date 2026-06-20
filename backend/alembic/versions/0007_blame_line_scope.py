"""blame line scope: blame_explanations 캐시 키에 line_history_hash 추가

Revision ID: 0007_blame_line_scope
Revises: 0006_commit_issues
Create Date: 2026-06-20

여러 번 수정된 줄(멀티 리비전)은 '이력 반영' 설명이 줄마다 달라야 한다. 기존 (file_id, commit_id)
2-키 캐시는 같은 커밋의 다른 줄에 잘못 적중하므로, 멀티 리비전 줄은 캐시를 통째로 우회해
매번 Bedrock 을 재호출했다(비용·지연).

이 마이그레이션은 line_history_hash 컬럼을 추가하고 캐시 키를 3-키로 확장한다:
  · line_history_hash='' → 커밋×파일 스코프(단일 리비전 줄들이 설명 공유) — 기존 동작 유지.
  · line_history_hash=<해시> → 라인 스코프(멀티 리비전 줄) — 줄 단위로 분리 저장/적중.
기존 행은 server_default '' 로 자동 채워져 커밋 스코프로 그대로 유효하다(재계산 불필요).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0007_blame_line_scope"
down_revision: Union[str, None] = "0006_commit_issues"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "blame_explanations",
        sa.Column("line_history_hash", sa.String(length=64), nullable=False, server_default=""),
    )
    op.drop_constraint("uq_blame_file_commit", "blame_explanations", type_="unique")
    op.create_unique_constraint(
        "uq_blame_file_commit_line",
        "blame_explanations",
        ["file_id", "commit_id", "line_history_hash"],
    )


def downgrade() -> None:
    # 라인 스코프 행(해시≠'')은 2-키 유니크와 충돌하므로 먼저 제거한 뒤 키를 되돌린다.
    op.execute("DELETE FROM blame_explanations WHERE line_history_hash <> ''")
    op.drop_constraint("uq_blame_file_commit_line", "blame_explanations", type_="unique")
    op.create_unique_constraint(
        "uq_blame_file_commit", "blame_explanations", ["file_id", "commit_id"]
    )
    op.drop_column("blame_explanations", "line_history_hash")
