"""source scanner and mail ingestion tables

Revision ID: 20260220_0002
Revises: 20260220_0001
Create Date: 2026-02-20
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260220_0002"
down_revision: Union[str, None] = "20260220_0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "source_file_states",
        sa.Column("path", sa.String(length=1024), nullable=False),
        sa.Column("source_type", sa.String(length=32), nullable=False, server_default="nas"),
        sa.Column("mtime_ns", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("size", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("path"),
    )

    op.create_table(
        "mail_processed_messages",
        sa.Column("message_id", sa.String(length=128), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("message_id"),
    )

    op.create_table(
        "mail_ingestion_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("message_id", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("subject", sa.String(length=512), nullable=False, server_default=""),
        sa.Column("from_addr", sa.String(length=512), nullable=False, server_default=""),
        sa.Column("attachment_name", sa.String(length=512), nullable=False, server_default=""),
        sa.Column("attachment_path", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="created"),
        sa.Column("detail", sa.String(length=240), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_mail_ingestion_events_message_id", "mail_ingestion_events", ["message_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_mail_ingestion_events_message_id", table_name="mail_ingestion_events")
    op.drop_table("mail_ingestion_events")
    op.drop_table("mail_processed_messages")
    op.drop_table("source_file_states")
