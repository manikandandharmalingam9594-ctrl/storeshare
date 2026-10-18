from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import argon2
from argon2 import PasswordHasher
from dotenv import load_dotenv
import jwt

# Load environment configuration (.env) from project root if present
PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(dotenv_path=PROJECT_ROOT / ".env")

# Password hashing with Argon2id
# PasswordHasher defaults to Argon2id (Type.ID)
_hasher = PasswordHasher(
    time_cost=3,
    memory_cost=65536,
    parallelism=4,
    hash_len=32,
    type=argon2.Type.ID,
)

# JWT configuration
JWT_ALGORITHM: str = "HS256"
DEFAULT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 60


def get_jwt_secret() -> str:
    """
    Retrieve JWT secret from environment variables.
    Fails securely and loudly if unconfigured. Never defaults silently.
    """
    secret = os.environ.get("STORESHARE_JWT_SECRET") or os.environ.get("JWT_SECRET")
    if not secret or not secret.strip():
        raise RuntimeError(
            "CRITICAL SECURITY CONFIGURATION ERROR: JWT secret is missing. "
            "Please configure the STORESHARE_JWT_SECRET environment variable."
        )
    return secret.strip()


def hash_password(password: str) -> str:
    """
    Hash a plaintext password using Argon2id.
    Never logs or exposes the plaintext password.
    """
    return _hasher.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """
    Securely verify a password against its Argon2id hash.
    Returns False on mismatch, invalid hash, or verification error.
    Never logs password content.
    """
    try:
        return _hasher.verify(hashed_password, plain_password)
    except (argon2.exceptions.VerifyMismatchError, argon2.exceptions.InvalidHashError):
        return False
    except Exception:
        return False


def create_access_token(
    user_id: int,
    email: str,
    role: str,
    expires_delta: Optional[timedelta] = None,
) -> Tuple[str, int]:
    """
    Create a signed JWT token containing standard claims (sub, email, role, iat, exp).
    Returns a tuple of (token_string, expires_in_seconds).
    """
    secret = get_jwt_secret()
    now = datetime.now(timezone.utc)

    if expires_delta is not None:
        expire = now + expires_delta
        expires_in = int(expires_delta.total_seconds())
    else:
        expire_minutes = int(
            os.environ.get(
                "STORESHARE_ACCESS_TOKEN_EXPIRE_MINUTES",
                DEFAULT_ACCESS_TOKEN_EXPIRE_MINUTES,
            )
        )
        expire = now + timedelta(minutes=expire_minutes)
        expires_in = expire_minutes * 60

    payload: Dict[str, Any] = {
        "sub": str(user_id),
        "email": email,
        "role": role,
        "iat": int(now.timestamp()),
        "exp": int(expire.timestamp()),
    }

    token = jwt.encode(payload, secret, algorithm=JWT_ALGORITHM)
    return token, expires_in


def decode_access_token(token: str) -> Dict[str, Any]:
    """
    Validate and decode a signed JWT token using fixed HS256 algorithm.
    Validates signature, expiration, and required claims.
    Raises ValueError on token verification failure.
    """
    secret = get_jwt_secret()
    try:
        payload = jwt.decode(
            token,
            secret,
            algorithms=[JWT_ALGORITHM],
            options={
                "verify_signature": True,
                "verify_exp": True,
                "require": ["exp", "sub", "role", "email"],
            },
        )
        return payload
    except jwt.ExpiredSignatureError as exc:
        raise ValueError("Token has expired") from exc
    except jwt.InvalidTokenError as exc:
        raise ValueError(f"Invalid token: {str(exc)}") from exc
