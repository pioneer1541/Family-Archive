"""add username to users and seed default admin

Revision ID: 20260309_0016
Revises: 20260305_0015
Create Date: 2026-03-09
"""

from typing import Sequence, Union

import bcrypt
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260309_0016"
down_revision: Union[str, None] = "20260305_0015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _normalize_username(raw: str) -> str:
    value = str(raw or "").strip().lower()
    if not value:
        return "user"
    out = []
    for ch in value:
        if ch.isalnum() or ch in {"_", "-", "."}:
            out.append(ch)
        else:
            out.append("_")
    normalized = "".join(out).strip("._-")
    return normalized or "user"


def upgrade() -> None:
    conn = op.get_bind()
    dialect = conn.dialect.name

    op.add_column("users", sa.Column("username", sa.String(length=64), nullable=True))

    rows = conn.execute(sa.text("SELECT id, email FROM users ORDER BY created_at ASC")).fetchall()
    used: set[str] = set()
    for row in rows:
        user_id = str(row[0])
        email = str(row[1] or "").strip().lower()
        base = "admin" if email == "admin@local" else _normalize_username(email.split("@", 1)[0] if "@" in email else email)
        candidate = base
        suffix = 1
        while candidate in used:
            suffix += 1
            candidate = f"{base}_{suffix}"
        used.add(candidate)
        conn.execute(sa.text("UPDATE users SET username = :username WHERE id = :user_id"), {"username": candidate, "user_id": user_id})

    if dialect == "postgresql":
        op.alter_column("users", "email", existing_type=sa.String(length=255), nullable=True)
        op.alter_column("users", "username", existing_type=sa.String(length=64), nullable=False)
    else:
        with op.batch_alter_table("users") as batch_op:
            batch_op.alter_column("email", existing_type=sa.String(length=255), nullable=True)
            batch_op.alter_column("username", existing_type=sa.String(length=64), nullable=False)

    op.create_index("ix_users_username", "users", ["username"], unique=True)

    # Ensure default admin/admin exists.
    admin = conn.execute(
        sa.text(
            "SELECT id FROM users "
            "WHERE username = 'admin' AND deleted_at IS NULL "
            "ORDER BY created_at ASC LIMIT 1"
        )
    ).fetchone()
    if admin is None:
        import uuid

        admin_hash = bcrypt.hashpw("admin".encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")
        conn.execute(
            sa.text(
                "INSERT INTO users (id, username, email, password_hash, role, is_active, created_at, updated_at) "
                "VALUES (:id, 'admin', NULL, :password_hash, 'admin', true, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            ),
            {"id": str(uuid.uuid4()), "password_hash": admin_hash},
        )


def downgrade() -> None:
    conn = op.get_bind()
    dialect = conn.dialect.name

    conn.execute(sa.text("DELETE FROM users WHERE email IS NULL AND username = 'admin'"))

    op.drop_index("ix_users_username", table_name="users")

    if dialect == "postgresql":
        op.alter_column("users", "email", existing_type=sa.String(length=255), nullable=False)
    else:
        with op.batch_alter_table("users") as batch_op:
            batch_op.alter_column("email", existing_type=sa.String(length=255), nullable=False)

    op.drop_column("users", "username")
