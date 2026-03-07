"""Add llm_providers table

Revision ID: 20260303_0013
Revises: 20260228_0012
Create Date: 2026-03-03
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "20260303_0013"
down_revision: Union[str, None] = "20260228_0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """创建 llm_providers 表"""
    op.create_table(
        "llm_providers",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("provider_type", sa.String(length=50), nullable=False),
        sa.Column("base_url", sa.String(length=512), nullable=False),
        sa.Column("api_key_encrypted", sa.Text(), nullable=True),
        sa.Column("model_name", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.PrimaryKeyConstraint("id"),
    )
    
    # 创建索引
    op.create_index("ix_llm_providers_provider_type", "llm_providers", ["provider_type"])
    op.create_index("ix_llm_providers_is_active", "llm_providers", ["is_active"])
    op.create_index("ix_llm_providers_is_default", "llm_providers", ["is_default"])


def downgrade() -> None:
    """删除 llm_providers 表"""
    op.drop_index("ix_llm_providers_is_default", table_name="llm_providers")
    op.drop_index("ix_llm_providers_is_active", table_name="llm_providers")
    op.drop_index("ix_llm_providers_provider_type", table_name="llm_providers")
    op.drop_table("llm_providers")
