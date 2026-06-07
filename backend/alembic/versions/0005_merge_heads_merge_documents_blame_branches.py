"""documents · blame 두 줄기 병합 (분기 head 통합)

Revision ID: 0005_merge_heads
Revises: 0003_documents_file_data, 0004_blame_issue_attachments
Create Date: 2026-06-07 20:00:55.470400

0002 에서 documents(file_data) 와 blame(commit_grain→issue_attachments) 가 병렬로 갈라져
head 가 둘이 되었다. 스키마 변경 없이 두 줄기를 한 점으로 합쳐 이후 `upgrade head`(단수)·
autogenerate 가 모호하지 않게 만든다. upgrade/downgrade 는 비어 있다(병합 전용).
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = '0005_merge_heads'
down_revision: Union[str, None] = ('0003_documents_file_data', '0004_blame_issue_attachments')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
