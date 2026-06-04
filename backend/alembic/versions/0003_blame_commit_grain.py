"""blame commit grain: blame_explanations 캐시 키를 (file_id, line_no, commit_id) → (file_id, commit_id)

Revision ID: 0003_blame_commit_grain
Revises: 0002_backfill_trace
Create Date: 2026-06-04

컨텍스트 블레임 효율화:
 - "왜 바뀌었나"는 줄(line)이 아니라 커밋이 그 파일에 가한 변경의 속성이다. 줄은 그 커밋을
   찾기 위한 포인터(git blame)일 뿐이므로, 분석·저장 단위를 줄 → 커밋×파일 로 격상한다.
 - 같은 커밋이 바꾼 여러 줄이 설명 1개를 공유 → Bedrock 호출 수와 DB 행 수를 dedup.
 - 기존 캐시 행은 줄 단위라 의미가 달라졌으므로 비운다(다음 조회 때 커밋 단위로 재생성).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003_blame_commit_grain"
down_revision: Union[str, None] = "0002_backfill_trace"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 줄 단위로 쌓인 기존 캐시는 새 키 체계와 의미가 다르므로 비운다(커밋 단위로 재생성됨).
    op.execute("DELETE FROM blame_explanations")
    op.drop_constraint("uq_blame_file_line_commit", "blame_explanations", type_="unique")
    op.drop_column("blame_explanations", "line_no")
    op.create_unique_constraint(
        "uq_blame_file_commit", "blame_explanations", ["file_id", "commit_id"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_blame_file_commit", "blame_explanations", type_="unique")
    op.add_column(
        "blame_explanations",
        sa.Column("line_no", sa.Integer(), nullable=False, server_default="0"),
    )
    op.alter_column("blame_explanations", "line_no", server_default=None)
    op.create_unique_constraint(
        "uq_blame_file_line_commit",
        "blame_explanations",
        ["file_id", "line_no", "commit_id"],
    )
