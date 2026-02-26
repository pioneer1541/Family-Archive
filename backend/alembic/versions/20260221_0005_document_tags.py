"""document tags and indexing

Revision ID: 20260221_0005
Revises: 20260220_0004
Create Date: 2026-02-21
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260221_0005"
down_revision: Union[str, None] = "20260220_0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "document_tags",
        sa.Column("document_id", sa.String(length=36), nullable=False),
        sa.Column("tag_key", sa.String(length=128), nullable=False),
        sa.Column("family", sa.String(length=32), nullable=False),
        sa.Column("value", sa.String(length=96), nullable=False),
        sa.Column("origin", sa.String(length=16), nullable=False, server_default="auto"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"]),
        sa.PrimaryKeyConstraint("document_id", "tag_key"),
    )
    op.create_index("ix_document_tags_document_id", "document_tags", ["document_id"])
    op.create_index("ix_document_tags_family", "document_tags", ["family"])
    op.create_index("ix_document_tags_value", "document_tags", ["value"])


def downgrade() -> None:
    op.drop_index("ix_document_tags_value", table_name="document_tags")
    op.drop_index("ix_document_tags_family", table_name="document_tags")
    op.drop_index("ix_document_tags_document_id", table_name="document_tags")
    op.drop_table("document_tags")
