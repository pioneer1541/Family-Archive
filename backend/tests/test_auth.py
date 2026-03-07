from datetime import timedelta

from sqlalchemy import select

from app.auth import COOKIE_NAME, create_access_token, decode_access_token, verify_password
from app.db import SessionLocal
from app.models import User


def _get_user_by_email(email: str) -> User | None:
    with SessionLocal() as db:
        return db.execute(select(User).where(User.email == email.lower().strip())).scalar_one_or_none()


def _extract_cookie_value(set_cookie_header: str, key: str) -> str:
    for part in (set_cookie_header or "").split(";"):
        token = part.strip()
        if token.startswith(f"{key}="):
            return token.split("=", 1)[1]
    return ""


def test_register_success_first_user_becomes_admin_and_password_is_hashed(client):
    payload = {"email": "owner@example.com", "password": "StrongPass123!"}
    response = client.post("/api/v1/auth/register", json=payload)
    assert response.status_code == 201

    body = response.json()
    assert body["email"] == payload["email"]
    assert body["role"] == "admin"
    assert body["user_id"]
    assert body["created_at"]

    created = _get_user_by_email(payload["email"])
    assert created is not None
    assert created.password_hash != payload["password"]
    assert verify_password(payload["password"], created.password_hash)


def test_register_fails_without_auth_when_admin_exists(client):
    admin_payload = {"email": "admin@example.com", "password": "StrongPass123!"}
    first = client.post("/api/v1/auth/register", json=admin_payload)
    assert first.status_code == 201

    denied = client.post("/api/v1/auth/register", json={"email": "user@example.com", "password": "StrongPass123!"})
    assert denied.status_code == 401
    assert denied.json() == {"detail": "Not authenticated."}


def test_register_success_with_admin_session_and_duplicate_email_fails(client):
    admin_payload = {"email": "admin@example.com", "password": "StrongPass123!"}
    assert client.post("/api/v1/auth/register", json=admin_payload).status_code == 201
    assert client.post("/api/v1/auth/login", json=admin_payload).status_code == 200

    create_user_resp = client.post("/api/v1/auth/register", json={"email": "user@example.com", "password": "UserPass123!"})
    assert create_user_resp.status_code == 201
    assert create_user_resp.json()["role"] == "user"

    duplicate_resp = client.post("/api/v1/auth/register", json={"email": "user@example.com", "password": "UserPass123!"})
    assert duplicate_resp.status_code == 422
    assert duplicate_resp.json() == {"detail": "Email already exists."}


def test_login_success_sets_cookie_and_returns_auth_payload(client):
    payload = {"email": "member@example.com", "password": "StrongPass123!"}
    assert client.post("/api/v1/auth/register", json=payload).status_code == 201

    response = client.post("/api/v1/auth/login", json={"email": "MEMBER@example.com", "password": payload["password"]})
    assert response.status_code == 200
    body = response.json()
    assert body["setup_complete"] is True
    assert body["authenticated"] is True
    assert body["user"]["email"] == "member@example.com"

    set_cookie = response.headers.get("set-cookie", "")
    assert f"{COOKIE_NAME}=" in set_cookie
    assert "HttpOnly" in set_cookie
    assert "SameSite=lax" in set_cookie

    token = _extract_cookie_value(set_cookie, COOKIE_NAME)
    ctx = decode_access_token(token)
    assert ctx is not None
    assert ctx.email == "member@example.com"
    assert ctx.role == "admin"


def test_login_fails_with_invalid_credentials(client):
    payload = {"email": "member@example.com", "password": "StrongPass123!"}
    assert client.post("/api/v1/auth/register", json=payload).status_code == 201

    response = client.post("/api/v1/auth/login", json={"email": payload["email"], "password": "wrong-password"})
    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid credentials."}


def test_jwt_generation_and_verification(client):
    payload = {"email": "jwt@example.com", "password": "StrongPass123!"}
    register = client.post("/api/v1/auth/register", json=payload)
    assert register.status_code == 201

    user = _get_user_by_email(payload["email"])
    assert user is not None

    token = create_access_token(user)
    decoded = decode_access_token(token)
    assert decoded is not None
    assert decoded.user_id == user.id
    assert decoded.email == user.email
    assert decoded.role == user.role

    expired_token = create_access_token(user, expires_delta=timedelta(seconds=-1))
    assert decode_access_token(expired_token) is None


def test_error_response_format_for_validation_errors(client):
    response = client.post("/api/v1/auth/register", json={"email": "x@x.com", "password": "123"})
    assert response.status_code == 422
    body = response.json()
    assert isinstance(body.get("detail"), list)
    assert any(item.get("loc", [])[-1] == "password" for item in body["detail"])
