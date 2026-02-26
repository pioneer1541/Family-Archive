"""ignored ingestion paths for queue delete semantics

Revision ID: 20260220_0004
Revises: 20260220_0003
Create Date: 2026-02-20
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260220_0004"
down_revision: Union[str, None] = "20260220_0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ignored_ingestion_paths",
        sa.Column("path", sa.String(length=1024), nullable=False),
        sa.Column("reason", sa.String(length=120), nullable=False, server_default="queue_deleted"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("path"),
    )


def downgrade() -> None:
    op.drop_table("ignored_ingestion_paths")
