"""add gmail_credentials table

Revision ID: 20260305_0015
Revises: 20260305_0014
Create Date: 2026-03-05

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '20260305_0015'
down_revision: Union[str, None] = '20260305_0014'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if "gmail_credentials" in inspector.get_table_names():
        return

    op.create_table(
        'gmail_credentials',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('name', sa.String(128), nullable=False),
        sa.Column('client_id', sa.String(256), nullable=False),
        sa.Column('client_secret_encrypted', sa.Text(), nullable=False),
        sa.Column('redirect_uri', sa.String(512), nullable=False, server_default='http://localhost'),
        sa.Column('token_encrypted', sa.Text(), nullable=True),
        sa.Column('refresh_token_encrypted', sa.Text(), nullable=True),
        sa.Column('token_uri', sa.String(256), nullable=False, server_default='https://oauth2.googleapis.com/token'),
        sa.Column('auth_uri', sa.String(256), nullable=False, server_default='https://accounts.google.com/o/oauth2/auth'),
        sa.Column('scopes', sa.Text(), nullable=False, server_default='https://www.googleapis.com/auth/gmail.readonly'),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
    )


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if "gmail_credentials" in inspector.get_table_names():
        op.drop_table('gmail_credentials')
