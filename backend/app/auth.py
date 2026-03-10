"""
Authentication module — bcrypt password + JWT cookie.

Supports multi-user authentication with role-based access control.
Password hashes are stored in the users table.
JWT contains user_id and role for authorization.
"""

import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Optional

import bcrypt
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AppSetting, User
from app.utils.encryption import decrypt, encrypt

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
_DEFAULT_BCRYPT_ROUNDS = 12


def _get_bcrypt_rounds() -> int:
    raw = os.environ.get("FAMILY_VAULT_BCRYPT_ROUNDS", str(_DEFAULT_BCRYPT_ROUNDS))
    try:
        rounds = int(raw)
    except (TypeError, ValueError):
        return _DEFAULT_BCRYPT_ROUNDS
    # bcrypt valid cost range is 4..31.
    return min(31, max(4, rounds))


def normalize_username(username: str) -> str:
    return str(username or "").strip().lower()


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class UserContext:
    """User context extracted from JWT token."""

    user_id: str
    username: str
    role: str


# ---------------------------------------------------------------------------
# Password helpers
# ---------------------------------------------------------------------------


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt(rounds=_get_bcrypt_rounds())).decode()


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode(), hashed.encode())
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Setup / bootstrap helpers
# ---------------------------------------------------------------------------


def ensure_default_admin(db: Session) -> User:
    """Ensure a default active admin user exists: admin/admin."""
    admin_user = db.execute(
        select(User).where(User.username == "admin", User.deleted_at.is_(None))
    ).scalar_one_or_none()
    if admin_user is not None:
        return admin_user

    import uuid

    admin_user = User(
        id=str(uuid.uuid4()),
        username="admin",
        email=None,
        password_hash=hash_password("admin"),
        role="admin",
        is_active=True,
    )
    db.add(admin_user)

    # Keep legacy setting in sync for old compatibility paths.
    row = db.get(AppSetting, _ADMIN_PASSWORD_KEY)
    if row is None:
        row = AppSetting(key=_ADMIN_PASSWORD_KEY, value=admin_user.password_hash, updated_at=datetime.now(UTC))
        db.add(row)
    else:
        row.value = admin_user.password_hash
        row.updated_at = datetime.now(UTC)

    db.commit()
    db.refresh(admin_user)
    return admin_user


def is_setup_complete(db: Session) -> bool:
    """Return True if at least one active admin user exists."""
    result = db.execute(
        select(User).where(User.role == "admin", User.is_active.is_(True), User.deleted_at.is_(None)).limit(1)
    ).scalar()
    if result is not None:
        return True

    # Initialization-only fallback: this is only for setup completeness checks,
    # not a credential verification/authentication path.
    # Fall back to legacy admin_password_hash in app_settings.
    row = db.get(AppSetting, _ADMIN_PASSWORD_KEY)
    if row is not None and bool(row.value):
        return True

    # Fresh system: initialize default admin/admin.
    ensure_default_admin(db)
    return True


def set_admin_password(plain: str, db: Session) -> None:
    """Hash and persist the admin password (creates admin user or updates existing)."""
    admin_user = ensure_default_admin(db)
    hashed = hash_password(plain)
    admin_user.password_hash = hashed
    admin_user.updated_at = datetime.now(UTC)

    row = db.get(AppSetting, _ADMIN_PASSWORD_KEY)
    if row is None:
        row = AppSetting(key=_ADMIN_PASSWORD_KEY, value=hashed, updated_at=datetime.now(UTC))
        db.add(row)
    else:
        row.value = hashed
        row.updated_at = datetime.now(UTC)

    db.commit()


def verify_admin_password(plain: str, db: Session) -> bool:
    """Return True if plain matches the stored bcrypt hash for admin."""
    admin_user = ensure_default_admin(db)
    return verify_password(plain, admin_user.password_hash)


# ---------------------------------------------------------------------------
# User management
# ---------------------------------------------------------------------------


def get_user_by_username(db: Session, username: str) -> Optional[User]:
    normalized = normalize_username(username)
    if not normalized:
        return None
    return db.execute(select(User).where(User.username == normalized, User.deleted_at.is_(None))).scalar_one_or_none()


def get_user_by_email(db: Session, email: str) -> Optional[User]:
    """Get a user by email (case-insensitive)."""
    normalized_email = str(email or "").lower().strip()
    if not normalized_email:
        return None
    return db.execute(
        select(User).where(User.email == normalized_email, User.deleted_at.is_(None))
    ).scalar_one_or_none()


def get_user_by_id(db: Session, user_id: str) -> Optional[User]:
    """Get a user by ID."""
    return db.execute(select(User).where(User.id == user_id, User.deleted_at.is_(None))).scalar_one_or_none()


def create_user(db: Session, username: str, password: str, role: str = "user", email: str | None = None) -> User:
    """Create a new user with hashed password."""
    import uuid

    normalized_username = normalize_username(username)
    normalized_email = str(email or "").strip().lower() or None
    hashed = hash_password(password)
    user = User(
        id=str(uuid.uuid4()),
        username=normalized_username,
        email=normalized_email,
        password_hash=hashed,
        role=role,
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def list_users(db: Session) -> list[User]:
    return list(
        db.execute(select(User).where(User.deleted_at.is_(None)).order_by(User.created_at.asc())).scalars().all()
    )


def soft_delete_user(db: Session, user_id: str) -> bool:
    """Soft delete a user by setting deleted_at timestamp."""
    user = get_user_by_id(db, user_id)
    if user is None:
        return False
    user.deleted_at = datetime.now(UTC)
    user.is_active = False
    db.commit()
    return True


def update_user_password(db: Session, user_id: str, new_password: str) -> bool:
    """Update a user's password."""
    user = get_user_by_id(db, user_id)
    if user is None:
        return False
    hashed = hash_password(new_password)
    user.password_hash = hashed
    user.updated_at = datetime.now(UTC)

    # Keep legacy admin setting in sync for backward compatibility.
    if user.role == "admin":
        row = db.get(AppSetting, _ADMIN_PASSWORD_KEY)
        if row is None:
            row = AppSetting(key=_ADMIN_PASSWORD_KEY, value=hashed, updated_at=datetime.now(UTC))
            db.add(row)
        else:
            row.value = hashed
            row.updated_at = datetime.now(UTC)

    db.commit()
    return True


def authenticate_user(db: Session, username: str, password: str) -> Optional[User]:
    """Authenticate a user by username and password."""
    normalized = normalize_username(username)

    user = get_user_by_username(db, normalized)
    if user is None and "@" in normalized:
        # Backward compatibility for legacy email login.
        user = get_user_by_email(db, normalized)
    if user is None and normalized in {"admin", "admin@local"}:
        # Legacy compatibility: ensure admin exists and verify against legacy hash.
        admin_user = ensure_default_admin(db)
        if verify_admin_password(password, db):
            return admin_user
        return None

    if user is None or not user.is_active:
        return None
    if not verify_password(password, user.password_hash):
        return None
    return user


# ---------------------------------------------------------------------------
# JWT helpers
# ---------------------------------------------------------------------------


def create_access_token(user: User, expires_delta: Optional[timedelta] = None) -> str:
    """Create a JWT token for a user."""
    expire = datetime.now(UTC) + (expires_delta or timedelta(hours=_ACCESS_TOKEN_EXPIRE_HOURS))
    payload = {
        "sub": user.id,
        "username": user.username,
        "email": user.email or "",
        "role": user.role,
        "exp": expire,
    }
    return jwt.encode(payload, _SECRET_KEY, algorithm=_ALGORITHM)


def create_legacy_access_token(expires_delta: Optional[timedelta] = None) -> str:
    """Create a legacy JWT token for admin (backward compatibility)."""
    expire = datetime.now(UTC) + (expires_delta or timedelta(hours=_ACCESS_TOKEN_EXPIRE_HOURS))
    return jwt.encode(
        {"sub": "admin", "username": "admin", "role": "admin", "exp": expire},
        _SECRET_KEY,
        algorithm=_ALGORITHM,
    )


def decode_access_token(token: str) -> Optional[UserContext]:
    """Decode and validate a JWT token, returning user context."""
    try:
        payload = jwt.decode(token, _SECRET_KEY, algorithms=[_ALGORITHM])
        user_id = payload.get("sub")
        if not user_id:
            return None
        username = str(payload.get("username") or "").strip().lower()
        role = payload.get("role", "user")
        return UserContext(user_id=user_id, username=username, role=role)
    except JWTError:
        return None


def get_current_user(db: Session, token: Optional[str]) -> Optional[User]:
    """Get the current user from a JWT token."""
    if not token:
        return None
    ctx = decode_access_token(token)
    if ctx is None:
        return None
    return get_user_by_id(db, ctx.user_id)


def encrypt_value(value: str | None) -> str | None:
    """Encrypt a sensitive runtime value for database storage."""
    return encrypt(value)


def decrypt_value(value: str | None) -> str | None:
    """Decrypt a sensitive runtime value from database storage."""
    return decrypt(value)
