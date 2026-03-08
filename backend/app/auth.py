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


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class UserContext:
    """User context extracted from JWT token."""

    user_id: str
    email: str
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
# Setup state (legacy, for backward compatibility)
# ---------------------------------------------------------------------------


def is_setup_complete(db: Session) -> bool:
    """Return True if at least one active admin user exists."""
    result = db.execute(
        select(User).where(User.role == "admin", User.is_active.is_(True), User.deleted_at.is_(None)).limit(1)
    ).scalar()
    if result is not None:
        return True
    # Fall back to legacy admin_password_hash in app_settings
    row = db.get(AppSetting, _ADMIN_PASSWORD_KEY)
    return row is not None and bool(row.value)


def set_admin_password(plain: str, db: Session) -> None:
    """Hash and persist the admin password (creates admin user or updates existing)."""
    hashed = hash_password(plain)

    # Try to find existing admin user
    admin_user = db.execute(
        select(User).where(User.email == "admin@local", User.deleted_at.is_(None))
    ).scalar_one_or_none()

    if admin_user is not None:
        admin_user.password_hash = hashed
        admin_user.updated_at = datetime.now(UTC)
    else:
        # Create new admin user
        import uuid

        admin_user = User(
            id=str(uuid.uuid4()),
            email="admin@local",
            password_hash=hashed,
            role="admin",
            is_active=True,
        )
        db.add(admin_user)

    # Also update legacy app_settings for backward compatibility
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
    # Try admin user first
    admin_user = db.execute(
        select(User).where(User.email == "admin@local", User.deleted_at.is_(None))
    ).scalar_one_or_none()

    if admin_user is not None:
        return verify_password(plain, admin_user.password_hash)

    # Fall back to legacy app_settings
    row = db.get(AppSetting, _ADMIN_PASSWORD_KEY)
    if row is None:
        return False
    return verify_password(plain, row.value)


# ---------------------------------------------------------------------------
# User management
# ---------------------------------------------------------------------------


def get_user_by_email(db: Session, email: str) -> Optional[User]:
    """Get a user by email (case-insensitive)."""
    normalized_email = email.lower().strip()
    return db.execute(select(User).where(User.email == normalized_email, User.deleted_at.is_(None))).scalar_one_or_none()


def get_user_by_id(db: Session, user_id: str) -> Optional[User]:
    """Get a user by ID."""
    return db.execute(select(User).where(User.id == user_id, User.deleted_at.is_(None))).scalar_one_or_none()


def create_user(db: Session, email: str, password: str, role: str = "user") -> User:
    """Create a new user with hashed password."""
    import uuid

    normalized_email = email.lower().strip()
    hashed = hash_password(password)
    user = User(
        id=str(uuid.uuid4()),
        email=normalized_email,
        password_hash=hashed,
        role=role,
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


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
    user.password_hash = hash_password(new_password)
    user.updated_at = datetime.now(UTC)
    db.commit()
    return True


def authenticate_user(db: Session, email: str, password: str) -> Optional[User]:
    """Authenticate a user by email and password."""
    user = get_user_by_email(db, email)
    if user is None:
        # Try legacy admin login
        if email.lower().strip() == "admin" or email.lower().strip() == "admin@local":
            if verify_admin_password(password, db):
                # Return or create admin user
                admin_user = get_user_by_email(db, "admin@local")
                if admin_user is None:
                    # Migrate legacy password to users table
                    admin_user = create_user(db, "admin@local", password, "admin")
                return admin_user
        return None
    if not user.is_active:
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
        "email": user.email,
        "role": user.role,
        "exp": expire,
    }
    return jwt.encode(payload, _SECRET_KEY, algorithm=_ALGORITHM)


def create_legacy_access_token(expires_delta: Optional[timedelta] = None) -> str:
    """Create a legacy JWT token for admin (backward compatibility)."""
    expire = datetime.now(UTC) + (expires_delta or timedelta(hours=_ACCESS_TOKEN_EXPIRE_HOURS))
    return jwt.encode(
        {"sub": "admin", "role": "admin", "exp": expire},
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
        email = payload.get("email", "")
        role = payload.get("role", "user")
        return UserContext(user_id=user_id, email=email, role=role)
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
