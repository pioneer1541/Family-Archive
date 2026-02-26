"""bill facts table for agent bill attention flow

Revision ID: 20260221_0007
Revises: 20260221_0006
Create Date: 2026-02-21
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "20260221_0007"
down_revision: Union[str, None] = "20260221_0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "bill_facts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("document_id", sa.String(length=36), nullable=False),
        sa.Column("vendor", sa.String(length=160), nullable=False, server_default=""),
        sa.Column("amount_due", sa.Float(), nullable=True),
        sa.Column("currency", sa.String(length=12), nullable=False, server_default="AUD"),
        sa.Column("due_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("billing_period_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("billing_period_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("payment_status", sa.String(length=24), nullable=False, server_default="unknown"),
        sa.Column("payment_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0"),
        sa.Column("evidence_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("extraction_version", sa.String(length=32), nullable=False, server_default="bill-facts-v1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("document_id"),
    )
    op.create_index("ix_bill_facts_document_id", "bill_facts", ["document_id"], unique=True)
    op.create_index("ix_bill_facts_due_date", "bill_facts", ["due_date"], unique=False)
    op.create_index("ix_bill_facts_payment_status", "bill_facts", ["payment_status"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_bill_facts_payment_status", table_name="bill_facts")
    op.drop_index("ix_bill_facts_due_date", table_name="bill_facts")
    op.drop_index("ix_bill_facts_document_id", table_name="bill_facts")
    op.drop_table("bill_facts")
