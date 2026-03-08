"""API dependencies for authentication and database sessions."""

from collections.abc import Generator

from fastapi import Cookie, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.auth import COOKIE_NAME, decode_access_token
from app.auth import get_current_user as _get_current_user_from_token
from app.db import SessionLocal
from app.models import User


def get_db() -> Generator[Session, None, None]:
    """Yield a SQLAlchemy session and always close it afterwards."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_current_user(
    db: Session = Depends(get_db),
    token: str | None = Cookie(default=None, alias=COOKIE_NAME),
) -> User:
    """Resolve authenticated user from JWT cookie or raise 401."""
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated.")
    if decode_access_token(token) is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid authentication token.")
    user = _get_current_user_from_token(db, token)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid authentication token.")
    return user


__all__ = ["get_db", "get_current_user"]
