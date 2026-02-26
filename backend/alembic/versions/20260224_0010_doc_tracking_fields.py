"""document ocr and longdoc tracking fields

Revision ID: 20260224_0010
Revises: 20260222_0009
Create Date: 2026-02-24
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "20260224_0010"
down_revision: Union[str, None] = "20260222_0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # OCR truncation tracking
    op.add_column("documents", sa.Column("ocr_pages_total", sa.Integer(), nullable=True))
    op.add_column("documents", sa.Column("ocr_pages_processed", sa.Integer(), nullable=True))

    # Long-document sampling tracking
    op.add_column("documents", sa.Column("longdoc_mode", sa.String(length=16), nullable=True))
    op.add_column("documents", sa.Column("longdoc_pages_total", sa.Integer(), nullable=True))
    op.add_column("documents", sa.Column("longdoc_pages_used", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("documents", "longdoc_pages_used")
    op.drop_column("documents", "longdoc_pages_total")
    op.drop_column("documents", "longdoc_mode")
    op.drop_column("documents", "ocr_pages_processed")
    op.drop_column("documents", "ocr_pages_total")
