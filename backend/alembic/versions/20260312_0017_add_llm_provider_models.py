"""Add llm_provider_models table

Revision ID: 20260312_0017
Revises: 20260309_0016
Create Date: 2026-03-12
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "20260312_0017"
down_revision: Union[str, None] = "20260309_0016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "llm_provider_models",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("provider_id", sa.String(length=36), nullable=False),
        sa.Column("model_name", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["provider_id"], ["llm_providers.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider_id", "model_name", name="uq_llm_provider_models_provider_model"),
    )
    op.create_index("ix_llm_provider_models_provider_id", "llm_provider_models", ["provider_id"])


def downgrade() -> None:
    op.drop_index("ix_llm_provider_models_provider_id", table_name="llm_provider_models")
    op.drop_table("llm_provider_models")
