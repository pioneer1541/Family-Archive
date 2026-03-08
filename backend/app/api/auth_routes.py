from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, HTTPException, Response, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.auth import (
    COOKIE_NAME,
    UserContext,
    authenticate_user,
    create_access_token,
    create_user,
    decode_access_token,
    get_current_user,
    list_users,
    normalize_username,
    soft_delete_user,
    update_user_password,
)
from app.config import get_settings
from app.models import User
from app.schemas import (
    AdminCreateUserRequest,
    AuthStatusResponse,
    UserChangePasswordRequest,
    UserListResponse,
    UserLoginRequest,
    UserRegisterRequest,
    UserResponse,
)

settings = get_settings()
router = APIRouter(prefix=f"{settings.api_prefix}/auth", tags=["auth"])


def _to_user_response(user: User) -> UserResponse:
    return UserResponse(
        user_id=user.id,
        username=user.username,
        email=user.email,
        role=user.role,
        created_at=user.created_at,
    )


def _auth_context_or_401(token: str | None) -> UserContext:
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated.")
    ctx = decode_access_token(token)
    if ctx is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token.",
        )
    return ctx


def _require_current_user(
    db: Session = Depends(get_db),
    token: str | None = Cookie(default=None, alias=COOKIE_NAME),
) -> User:
    _auth_context_or_401(token)
    user = get_current_user(db, token)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")
    return user


def _assert_username_available(db: Session, username: str) -> None:
    normalized_username = normalize_username(username)
    existing = db.execute(select(User).where(func.lower(User.username) == normalized_username)).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Username already exists.",
        )


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def register_user(
    payload: UserRegisterRequest,
    db: Session = Depends(get_db),
) -> UserResponse:
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Registration is disabled. Ask an administrator to create your account.",
    )


@router.post("/login", response_model=AuthStatusResponse)
def login(
    payload: UserLoginRequest,
    response: Response,
    db: Session = Depends(get_db),
) -> AuthStatusResponse:
    user = authenticate_user(db, payload.username, payload.password)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials.")

    token = create_access_token(user)
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
        max_age=86400,
    )
    return AuthStatusResponse(setup_complete=True, authenticated=True, user=_to_user_response(user))


@router.post("/logout", response_model=AuthStatusResponse)
def logout(response: Response) -> AuthStatusResponse:
    response.delete_cookie(
        key=COOKIE_NAME,
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
    )
    return AuthStatusResponse(setup_complete=True, authenticated=False, user=None)


@router.put("/password", response_model=AuthStatusResponse)
def change_password(
    payload: UserChangePasswordRequest,
    current_user: Annotated[User, Depends(_require_current_user)],
    db: Session = Depends(get_db),
) -> AuthStatusResponse:
    verified_user = authenticate_user(db, current_user.username, payload.old_password)
    if verified_user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials.")

    updated = update_user_password(db, current_user.id, payload.new_password)
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")
    refreshed_user = db.get(User, current_user.id)
    if refreshed_user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")
    return AuthStatusResponse(setup_complete=True, authenticated=True, user=_to_user_response(refreshed_user))


@router.delete("/account", response_model=AuthStatusResponse)
def delete_account(
    response: Response,
    current_user: Annotated[User, Depends(_require_current_user)],
    db: Session = Depends(get_db),
) -> AuthStatusResponse:
    deleted = soft_delete_user(db, current_user.id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")
    response.delete_cookie(
        key=COOKIE_NAME,
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
    )
    return AuthStatusResponse(setup_complete=True, authenticated=False, user=None)


@router.get("/me", response_model=UserResponse)
def get_me(
    current_user: Annotated[User, Depends(_require_current_user)],
) -> UserResponse:
    return _to_user_response(current_user)


def _require_admin(user: User) -> None:
    if user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin privileges required.")


@router.get("/users", response_model=UserListResponse)
def admin_list_users(
    current_user: Annotated[User, Depends(_require_current_user)],
    db: Session = Depends(get_db),
) -> UserListResponse:
    _require_admin(current_user)
    rows = list_users(db)
    items = [_to_user_response(item) for item in rows]
    return UserListResponse(total=len(items), items=items)


@router.post("/users", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def admin_create_user(
    payload: AdminCreateUserRequest,
    current_user: Annotated[User, Depends(_require_current_user)],
    db: Session = Depends(get_db),
) -> UserResponse:
    _require_admin(current_user)
    _assert_username_available(db, payload.username)
    try:
        user = create_user(
            db,
            username=payload.username,
            email=payload.email,
            password=payload.password,
            role=payload.role,
        )
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Username already exists.") from None
    return _to_user_response(user)


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def admin_delete_user(
    user_id: str,
    current_user: Annotated[User, Depends(_require_current_user)],
    db: Session = Depends(get_db),
) -> Response:
    _require_admin(current_user)
    if user_id == current_user.id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot delete current user.")

    target = db.execute(select(User).where(User.id == user_id, User.deleted_at.is_(None))).scalar_one_or_none()
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")

    if target.role == "admin":
        remaining_admins = db.scalar(
            select(func.count())
            .select_from(User)
            .where(
                User.role == "admin",
                User.deleted_at.is_(None),
                User.is_active.is_(True),
                User.id != target.id,
            )
        )
        if int(remaining_admins or 0) <= 0:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot delete last admin user.")

    deleted = soft_delete_user(db, user_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
