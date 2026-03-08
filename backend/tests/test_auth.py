from datetime import timedelta

from sqlalchemy import select

from app.auth import COOKIE_NAME, create_access_token, decode_access_token, verify_password
from app.db import SessionLocal
from app.models import User


def _get_user_by_username(username: str) -> User | None:
    with SessionLocal() as db:
        return db.execute(select(User).where(User.username == username.lower().strip())).scalar_one_or_none()


def _extract_cookie_value(set_cookie_header: str, key: str) -> str:
    for part in (set_cookie_header or "").split(";"):
        token = part.strip()
        if token.startswith(f"{key}="):
            return token.split("=", 1)[1]
    return ""


def test_default_admin_created_and_can_login(client):
    response = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin"})
    assert response.status_code == 200

    set_cookie = response.headers.get("set-cookie", "")
    assert f"{COOKIE_NAME}=" in set_cookie


def test_register_success_password_hashed(client):
    payload = {"username": "alice", "password": "StrongPass123!"}
    response = client.post("/api/v1/auth/register", json=payload)
    assert response.status_code == 201

    body = response.json()
    assert body["username"] == payload["username"]
    assert body["role"] == "user"
    assert body["user_id"]

    created = _get_user_by_username(payload["username"])
    assert created is not None
    assert created.password_hash != payload["password"]
    assert verify_password(payload["password"], created.password_hash)


def test_register_duplicate_username_fails(client):
    payload = {"username": "bob", "password": "StrongPass123!"}
    assert client.post("/api/v1/auth/register", json=payload).status_code == 201

    duplicate_resp = client.post("/api/v1/auth/register", json=payload)
    assert duplicate_resp.status_code == 422
    assert duplicate_resp.json() == {"detail": "Username already exists."}


def test_login_fails_with_invalid_credentials(client):
    payload = {"username": "member", "password": "StrongPass123!"}
    assert client.post("/api/v1/auth/register", json=payload).status_code == 201

    response = client.post("/api/v1/auth/login", json={"username": payload["username"], "password": "wrong-password"})
    assert response.status_code == 401


def test_jwt_generation_and_verification(client):
    payload = {"username": "jwtuser", "password": "StrongPass123!"}
    register = client.post("/api/v1/auth/register", json=payload)
    assert register.status_code == 201

    user = _get_user_by_username(payload["username"])
    assert user is not None

    token = create_access_token(user)
    decoded = decode_access_token(token)
    assert decoded is not None
    assert decoded.user_id == user.id
    assert decoded.username == user.username
    assert decoded.role == user.role

    expired_token = create_access_token(user, expires_delta=timedelta(seconds=-1))
    assert decode_access_token(expired_token) is None


def test_change_password_patch_works(client):
    payload = {"username": "changepw", "password": "StrongPass123!"}
    assert client.post("/api/v1/auth/register", json=payload).status_code == 201
    login = client.post("/api/v1/auth/login", json=payload)
    assert login.status_code == 200

    change = client.patch(
        "/api/v1/auth/password",
        json={"old_password": payload["password"], "new_password": "NewStrongPass123!"},
    )
    assert change.status_code == 200

    relogin = client.post("/api/v1/auth/login", json={"username": payload["username"], "password": "NewStrongPass123!"})
    assert relogin.status_code == 200


def test_admin_user_management_create_list_delete(client):
    login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin"})
    assert login.status_code == 200

    create = client.post(
        "/api/v1/auth/users",
        json={"username": "newuser", "password": "StrongPass123!", "role": "user"},
    )
    assert create.status_code == 201
    created_user_id = create.json()["user_id"]

    listed = client.get("/api/v1/auth/users")
    assert listed.status_code == 200
    body = listed.json()
    assert body["total"] >= 2
    assert any(item["username"] == "newuser" for item in body["items"])

    deleted = client.delete(f"/api/v1/auth/users/{created_user_id}")
    assert deleted.status_code == 204


def test_non_admin_cannot_manage_users(client):
    payload = {"username": "plainuser", "password": "StrongPass123!"}
    assert client.post("/api/v1/auth/register", json=payload).status_code == 201
    login = client.post("/api/v1/auth/login", json=payload)
    assert login.status_code == 200

    r1 = client.get("/api/v1/auth/users")
    assert r1.status_code == 403

    r2 = client.post("/api/v1/auth/users", json={"username": "xuser", "password": "StrongPass123!"})
    assert r2.status_code == 403


def test_error_response_format_for_validation_errors(client):
    response = client.post("/api/v1/auth/register", json={"username": "u1", "password": "123"})
    assert response.status_code == 422
    body = response.json()
    assert isinstance(body.get("detail"), list)
    assert any(item.get("loc", [])[-1] == "password" for item in body["detail"])


def test_me_response_returns_username(client):
    payload = {"username": "meuser", "password": "StrongPass123!"}
    assert client.post("/api/v1/auth/register", json=payload).status_code == 201
    login = client.post("/api/v1/auth/login", json=payload)
    assert login.status_code == 200

    cookie = _extract_cookie_value(login.headers.get("set-cookie", ""), COOKIE_NAME)
    me = client.get("/api/v1/auth/me", cookies={COOKIE_NAME: cookie})
    assert me.status_code == 200
    assert me.json()["username"] == "meuser"
