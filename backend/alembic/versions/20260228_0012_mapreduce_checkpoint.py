"""Add map-reduce checkpoint fields to documents table

Revision ID: 20260228_0012
Revises: 20260228_0011
Create Date: 2026-02-28
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "20260228_0012"
down_revision: Union[str, None] = "20260228_0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # All three columns are nullable=False with safe defaults so existing rows
    # are automatically back-filled on SQLite (ALTER TABLE ADD COLUMN with
    # DEFAULT is always backward compatible).
    op.add_column("documents", sa.Column("mapreduce_page_summaries_json", sa.Text(), nullable=False, server_default="[]"))
    op.add_column("documents", sa.Column("mapreduce_section_summaries_json", sa.Text(), nullable=False, server_default="[]"))
    op.add_column("documents", sa.Column("mapreduce_job_status", sa.String(length=32), nullable=False, server_default=""))


def downgrade() -> None:
    op.drop_column("documents", "mapreduce_job_status")
    op.drop_column("documents", "mapreduce_section_summaries_json")
    op.drop_column("documents", "mapreduce_page_summaries_json")
