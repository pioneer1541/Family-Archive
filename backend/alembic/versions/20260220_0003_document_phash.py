"""add document phash for image content dedup

Revision ID: 20260220_0003
Revises: 20260220_0002
Create Date: 2026-02-20
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260220_0003"
down_revision: Union[str, None] = "20260220_0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("phash", sa.String(length=32), nullable=True))
    op.create_index("ix_documents_phash", "documents", ["phash"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_documents_phash", table_name="documents")
    op.drop_column("documents", "phash")
