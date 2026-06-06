"""documents: add file_data column (store binary in DB)

Revision ID: 0003_documents_file_data
Revises: 0002_backfill_trace
Create Date: 2026-06-04
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003_documents_file_data"
down_revision: Union[str, None] = "0002_backfill_trace"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("file_data", sa.LargeBinary(), nullable=True))


def downgrade() -> None:
    op.drop_column("documents", "file_data")
