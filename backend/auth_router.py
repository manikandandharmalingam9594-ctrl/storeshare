from typing import Any, Dict
import logging

from fastapi import APIRouter, Depends, HTTPException, status

from backend.database import create_user, get_user_by_email
from backend.dependencies import get_current_active_user, require_admin
from backend.schemas import (
    CreateProviderRequest,
    TokenResponse,
    UserLoginRequest,
    UserRegisterRequest,
    UserResponse,
)
from backend.security import create_access_token, hash_password, verify_password

logger = logging.getLogger("store-share-auth")
router = APIRouter(prefix="/api", tags=["Authentication"])


@router.post(
    "/auth/register",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register Consumer Account",
)
def register_consumer(payload: UserRegisterRequest) -> UserResponse:
    """
    Public registration endpoint strictly for CONSUMER role.
    Role parameter is forbidden on input and guaranteed to be CONSUMER.
    Returns HTTP 409 Conflict if email is already registered.
    """
    existing_user = get_user_by_email(payload.email)
    if existing_user:
        logger.warning(f"Registration attempt with duplicate email: {payload.email}")
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email address already exists.",
        )

    # Hash with Argon2id; never store or log plaintext
    password_hash = hash_password(payload.password)

    # Force role to CONSUMER - client cannot override
    new_user = create_user(
        email=payload.email,
        password_hash=password_hash,
        role="CONSUMER",
        is_active=True,
    )

    logger.info(f"Registered new consumer user id={new_user['id']}")
    return UserResponse(
        id=new_user["id"],
        email=new_user["email"],
        role=new_user["role"],
        is_active=bool(new_user["is_active"]),
        created_at=str(new_user["created_at"]) if new_user.get("created_at") else None,
    )


@router.post(
    "/auth/login",
    response_model=TokenResponse,
    summary="Authenticate User and Issue JWT",
)
def login(payload: UserLoginRequest) -> TokenResponse:
    """
    Authenticate user credentials and issue a signed JWT.
    Returns HTTP 401 for invalid credentials or inactive accounts.
    """
    user = get_user_by_email(payload.email)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not verify_password(payload.password, user["password_hash"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not user.get("is_active"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Account is inactive. Please contact system administrator.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token, expires_in = create_access_token(
        user_id=user["id"],
        email=user["email"],
        role=user["role"],
    )

    user_resp = UserResponse(
        id=user["id"],
        email=user["email"],
        role=user["role"],
        is_active=bool(user["is_active"]),
        created_at=str(user["created_at"]) if user.get("created_at") else None,
    )

    return TokenResponse(
        access_token=token,
        token_type="bearer",
        expires_in=expires_in,
        user=user_resp,
    )


@router.get(
    "/auth/me",
    response_model=UserResponse,
    summary="Get Current Authenticated User",
)
def get_me(
    current_user: Dict[str, Any] = Depends(get_current_active_user),
) -> UserResponse:
    """
    Return identity, role, and account status for current active authenticated user.
    """
    return UserResponse(
        id=current_user["id"],
        email=current_user["email"],
        role=current_user["role"],
        is_active=bool(current_user["is_active"]),
        created_at=str(current_user["created_at"]) if current_user.get("created_at") else None,
    )


@router.post(
    "/admin/providers",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Provision Provider Account (Admin Only)",
)
def create_provider_account(
    payload: CreateProviderRequest,
    admin_user: Dict[str, Any] = Depends(require_admin),
) -> UserResponse:
    """
    Admin-only endpoint to provision new storage provider accounts.
    Enforces that providers cannot self-register.
    """
    existing_user = get_user_by_email(payload.email)
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email address already exists.",
        )

    password_hash = hash_password(payload.password)
    provider_user = create_user(
        email=payload.email,
        password_hash=password_hash,
        role="PROVIDER",
        is_active=True,
    )

    logger.info(f"Admin id={admin_user['id']} provisioned provider id={provider_user['id']}")
    return UserResponse(
        id=provider_user["id"],
        email=provider_user["email"],
        role=provider_user["role"],
        is_active=bool(provider_user["is_active"]),
        created_at=str(provider_user["created_at"]) if provider_user.get("created_at") else None,
    )
