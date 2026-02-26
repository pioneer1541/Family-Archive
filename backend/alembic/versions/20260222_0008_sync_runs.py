"""sync run tables for dashboard instant sync tracking

Revision ID: 20260222_0008
Revises: 20260221_0007
Create Date: 2026-02-22
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "20260222_0008"
down_revision: Union[str, None] = "20260221_0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "sync_runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="running"),
        sa.Column("error_code", sa.String(length=120), nullable=True),
        sa.Column("nas_job_id", sa.String(length=36), nullable=False, server_default=""),
        sa.Column("mail_job_id", sa.String(length=36), nullable=False, server_default=""),
        sa.Column("nas_summary_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("mail_summary_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "sync_run_items",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("source_type", sa.String(length=16), nullable=False, server_default="nas"),
        sa.Column("source_path", sa.Text(), nullable=False, server_default=""),
        sa.Column("file_name", sa.String(length=512), nullable=False, server_default=""),
        sa.Column("file_size", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("doc_id", sa.String(length=36), nullable=True),
        sa.Column("stage", sa.String(length=24), nullable=False, server_default="discovered"),
        sa.Column("detail", sa.String(length=240), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["run_id"], ["sync_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index("ix_sync_run_items_run_id", "sync_run_items", ["run_id"], unique=False)
    op.create_index("ix_sync_run_items_stage", "sync_run_items", ["stage"], unique=False)
    op.create_index("ix_sync_run_items_source_path", "sync_run_items", ["source_path"], unique=False)
    op.create_index("ix_sync_run_items_doc_id", "sync_run_items", ["doc_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_sync_run_items_doc_id", table_name="sync_run_items")
    op.drop_index("ix_sync_run_items_source_path", table_name="sync_run_items")
    op.drop_index("ix_sync_run_items_stage", table_name="sync_run_items")
    op.drop_index("ix_sync_run_items_run_id", table_name="sync_run_items")
    op.drop_table("sync_run_items")
    op.drop_table("sync_runs")
