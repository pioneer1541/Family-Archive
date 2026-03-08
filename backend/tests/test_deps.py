from datetime import timedelta

import pytest
from fastapi import HTTPException

from app.api import deps
from app.auth import create_access_token, create_user
from app.db import SessionLocal


def test_get_db_yields_and_closes_session(monkeypatch):
    class FakeSession:
        def __init__(self) -> None:
            self.closed = False

        def close(self) -> None:
            self.closed = True

    created: list[FakeSession] = []

    def _factory() -> FakeSession:
        sess = FakeSession()
        created.append(sess)
        return sess

    monkeypatch.setattr(deps, "SessionLocal", _factory)

    gen = deps.get_db()
    session = next(gen)
    assert isinstance(session, FakeSession)
    assert session.closed is False

    try:
        next(gen)
    except StopIteration:
        pass

    assert created[0].closed is True


def test_get_current_user_success():
    with SessionLocal() as db:
        user = create_user(
            db,
            username="deps-success",
            email="deps-success@example.com",
            password="StrongPass123!",
            role="admin",
        )
        token = create_access_token(user)
        current = deps.get_current_user(db=db, token=token)

    assert current.id == user.id
    assert current.username == "deps-success"
    assert current.email == "deps-success@example.com"
    assert current.role == "admin"


def test_get_current_user_returns_401_when_missing_cookie():
    with SessionLocal() as db:
        with pytest.raises(HTTPException) as exc:
            deps.get_current_user(db=db, token=None)
    assert exc.value.status_code == 401
    assert exc.value.detail == "Not authenticated."


def test_get_current_user_returns_401_for_expired_token():
    with SessionLocal() as db:
        user = create_user(
            db,
            username="deps-expired",
            email="deps-expired@example.com",
            password="StrongPass123!",
            role="user",
        )
        expired = create_access_token(user, expires_delta=timedelta(seconds=-1))
        with pytest.raises(HTTPException) as exc:
            deps.get_current_user(db=db, token=expired)
    assert exc.value.status_code == 401
    assert exc.value.detail == "Invalid authentication token."
