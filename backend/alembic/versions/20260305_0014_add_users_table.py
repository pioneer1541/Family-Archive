"""add users table

Revision ID: 0014
Revises: 0013
Create Date: 2025-03-05

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '20260305_0014'
down_revision: Union[str, None] = '20260303_0013'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create users table
    op.create_table(
        'users',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('email', sa.String(255), nullable=False),
        sa.Column('password_hash', sa.String(128), nullable=False),
        sa.Column('role', sa.String(16), nullable=False, server_default='user'),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='1'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index('ix_users_email', 'users', ['email'], unique=True)
    op.create_index('ix_users_deleted_at', 'users', ['deleted_at'])

    # Migrate existing admin password from app_settings to users table
    conn = op.get_bind()
    result = conn.execute(sa.text("SELECT value FROM app_settings WHERE key = 'admin_password_hash'"))
    row = result.fetchone()
    if row is not None:
        password_hash = row[0]
        # Check if admin user already exists
        existing = conn.execute(sa.text("SELECT id FROM users WHERE email = 'admin@local'"))
        if existing.fetchone() is None:
            import uuid
            user_id = str(uuid.uuid4())
            conn.execute(
                sa.text(
                    "INSERT INTO users (id, email, password_hash, role, is_active, created_at, updated_at) "
                    "VALUES (:id, 'admin@local', :hash, 'admin', 1, datetime('now'), datetime('now'))"
                ),
                {'id': user_id, 'hash': password_hash}
            )


def downgrade() -> None:
    op.drop_index('ix_users_deleted_at', table_name='users')
    op.drop_index('ix_users_email', table_name='users')
    op.drop_table('users')
