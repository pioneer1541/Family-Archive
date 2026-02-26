"""document source availability cache and mail sync run attribution

Revision ID: 20260222_0009
Revises: 20260222_0008
Create Date: 2026-02-22
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "20260222_0009"
down_revision: Union[str, None] = "20260222_0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "documents",
        sa.Column("source_available_cached", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.add_column(
        "documents",
        sa.Column("source_checked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_documents_status_src_cached",
        "documents",
        ["status", "source_available_cached"],
        unique=False,
    )
    op.create_index(
        "ix_documents_category_src_cached",
        "documents",
        ["category_path", "source_available_cached"],
        unique=False,
    )

    op.add_column(
        "mail_ingestion_events",
        sa.Column("sync_run_id", sa.String(length=36), nullable=True),
    )
    op.create_index("ix_mail_ingestion_events_sync_run_id", "mail_ingestion_events", ["sync_run_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_mail_ingestion_events_sync_run_id", table_name="mail_ingestion_events")
    op.drop_column("mail_ingestion_events", "sync_run_id")

    op.drop_index("ix_documents_category_src_cached", table_name="documents")
    op.drop_index("ix_documents_status_src_cached", table_name="documents")
    op.drop_column("documents", "source_checked_at")
    op.drop_column("documents", "source_available_cached")

