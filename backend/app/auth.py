"""
Authentication module — bcrypt password + JWT cookie.

Password is stored in app_settings (key="admin_password_hash") as a bcrypt hash.
JWT is issued as an HttpOnly, SameSite=Lax cookie (no Bearer header needed).

If no password is set yet (setup_complete=False), the /v1/auth/setup endpoint
allows setting the initial password without authentication.
"""

import os
from datetime import UTC, datetime, timedelta
from typing import Optional

import bcrypt
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from app.models import AppSetting

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
_secret_key_raw = os.environ.get("FAMILY_VAULT_JWT_SECRET")
if not _secret_key_raw:
    raise ValueError(
        "\n\n[Family Vault] FAMILY_VAULT_JWT_SECRET is not set.\n"
        "Generate a secure secret with:\n"
        "    openssl rand -hex 32\n"
        "Then add it to your .env file or Docker environment.\n"
    )
_SECRET_KEY = _secret_key_raw
_ALGORITHM = "HS256"
_ACCESS_TOKEN_EXPIRE_HOURS = 24
COOKIE_NAME = "fkv_token"

_ADMIN_PASSWORD_KEY = "admin_password_hash"


# ---------------------------------------------------------------------------
# Password helpers
# ---------------------------------------------------------------------------


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode(), hashed.encode())
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Setup state
# ---------------------------------------------------------------------------


def is_setup_complete(db: Session) -> bool:
    """Return True if an admin password has been set."""
    row = db.get(AppSetting, _ADMIN_PASSWORD_KEY)
    return row is not None and bool(row.value)


def set_admin_password(plain: str, db: Session) -> None:
    """Hash and persist the admin password (creates or updates the row)."""
    hashed = hash_password(plain)
    row = db.get(AppSetting, _ADMIN_PASSWORD_KEY)
    if row is None:
        row = AppSetting(
            key=_ADMIN_PASSWORD_KEY, value=hashed, updated_at=datetime.now(UTC)
        )
        db.add(row)
    else:
        row.value = hashed
        row.updated_at = datetime.now(UTC)
    db.commit()


def verify_admin_password(plain: str, db: Session) -> bool:
    """Return True if plain matches the stored bcrypt hash."""
    row = db.get(AppSetting, _ADMIN_PASSWORD_KEY)
    if row is None:
        return False
    return verify_password(plain, row.value)


# ---------------------------------------------------------------------------
# JWT helpers
# ---------------------------------------------------------------------------


def create_access_token(expires_delta: Optional[timedelta] = None) -> str:
    expire = datetime.now(UTC) + (
        expires_delta or timedelta(hours=_ACCESS_TOKEN_EXPIRE_HOURS)
    )
    return jwt.encode(
        {"sub": "admin", "exp": expire}, _SECRET_KEY, algorithm=_ALGORITHM
    )


def decode_access_token(token: str) -> Optional[str]:
    """Return the 'sub' claim if valid, None otherwise."""
    try:
        payload = jwt.decode(token, _SECRET_KEY, algorithms=[_ALGORITHM])
        return payload.get("sub")
    except JWTError:
        return None
