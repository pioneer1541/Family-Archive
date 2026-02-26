"""document summary quality and version fields

Revision ID: 20260221_0006
Revises: 20260221_0005
Create Date: 2026-02-21
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260221_0006"
down_revision: Union[str, None] = "20260221_0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("summary_quality_state", sa.String(length=24), nullable=False, server_default="unknown"))
    op.add_column("documents", sa.Column("summary_last_error", sa.String(length=240), nullable=False, server_default=""))
    op.add_column("documents", sa.Column("summary_model", sa.String(length=64), nullable=False, server_default=""))
    op.add_column("documents", sa.Column("summary_version", sa.String(length=32), nullable=False, server_default="prompt-v2"))
    op.add_column("documents", sa.Column("category_version", sa.String(length=32), nullable=False, server_default="taxonomy-v1"))
    op.add_column("documents", sa.Column("name_version", sa.String(length=32), nullable=False, server_default="name-v2"))


def downgrade() -> None:
    op.drop_column("documents", "name_version")
    op.drop_column("documents", "category_version")
    op.drop_column("documents", "summary_version")
    op.drop_column("documents", "summary_model")
    op.drop_column("documents", "summary_last_error")
    op.drop_column("documents", "summary_quality_state")
