from enum import Enum
from pathlib import Path
import re
from typing import Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator


class UserRole(str, Enum):
    CONSUMER = "CONSUMER"
    PROVIDER = "PROVIDER"
    ADMIN = "ADMIN"


# Strict email regex pattern
EMAIL_REGEX = re.compile(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$")


class UserRegisterRequest(BaseModel):
    """
    Public registration request.
    Strictly forbids role specification from the client to prevent role-escalation attacks.
    """
    model_config = ConfigDict(extra="forbid")

    email: str = Field(..., description="User email address")
    password: str = Field(..., description="Plaintext password for registration")

    @field_validator("email")
    @classmethod
    def validate_and_normalize_email(cls, v: str) -> str:
        trimmed = v.strip()
        if not trimmed or not EMAIL_REGEX.match(trimmed):
            raise ValueError("Invalid email address format.")
        return trimmed.lower()

    @field_validator("password")
    @classmethod
    def validate_password_strength(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters long.")
        if len(v) > 128:
            raise ValueError("Password cannot exceed 128 characters.")
        if v.strip() == "":
            raise ValueError("Password cannot be empty or only whitespace.")
        return v


class UserLoginRequest(BaseModel):
    """
    Credentials for authenticating a user.
    """
    model_config = ConfigDict(extra="forbid")

    email: str = Field(..., description="Registered email address")
    password: str = Field(..., description="Account password")

    @field_validator("email")
    @classmethod
    def normalize_email(cls, v: str) -> str:
        return v.strip().lower()


class CreateProviderRequest(BaseModel):
    """
    Admin-only request to provision a new PROVIDER user account.
    """
    model_config = ConfigDict(extra="forbid")

    email: str = Field(..., description="Provider email address")
    password: str = Field(..., description="Provider initial password")

    @field_validator("email")
    @classmethod
    def validate_and_normalize_email(cls, v: str) -> str:
        trimmed = v.strip()
        if not trimmed or not EMAIL_REGEX.match(trimmed):
            raise ValueError("Invalid email address format.")
        return trimmed.lower()

    @field_validator("password")
    @classmethod
    def validate_password_strength(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters long.")
        if len(v) > 128:
            raise ValueError("Password cannot exceed 128 characters.")
        return v


class UserResponse(BaseModel):
    """
    Public-safe user representation.
    Never exposes password_hash.
    """
    id: int
    email: str
    role: str
    is_active: bool
    created_at: Optional[str] = None


class TokenResponse(BaseModel):
    """
    JWT Bearer token response.
    """
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserResponse


class ProviderSetupRequest(BaseModel):
    """
    Provider setup request to configure storage directory and quota.
    """
    model_config = ConfigDict(extra="forbid")

    storage_dir: str = Field(..., description="Local storage directory path on provider host")
    quota_gb: float = Field(..., description="Allocated storage quota in Gigabytes")
    action: str = Field(default="START", description="Action to perform: START, STOP, or UPDATE")

    @field_validator("storage_dir")
    @classmethod
    def validate_storage_dir(cls, v: str) -> str:
        trimmed = v.strip()
        if not trimmed:
            raise ValueError("Storage directory cannot be empty.")
        return trimmed

    @field_validator("quota_gb")
    @classmethod
    def validate_quota(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("Storage quota must be greater than 0 GB.")
        if v > 100000:
            raise ValueError("Storage quota cannot exceed 100,000 GB.")
        return round(v, 2)

    @field_validator("action")
    @classmethod
    def validate_action(cls, v: str) -> str:
        upper = v.strip().upper()
        if upper not in ("START", "STOP", "UPDATE"):
            raise ValueError("Action must be one of: START, STOP, UPDATE.")
        return upper


class ProviderHeartbeatRequest(BaseModel):
    """
    Outbound heartbeat report from the local Python provider agent.
    """
    model_config = ConfigDict(extra="forbid")

    allocated_bytes: int = Field(..., ge=0, description="Total storage bytes allocated to Store-Share")
    used_bytes: int = Field(..., ge=0, description="Actual bytes used by stored chunks")
    free_disk_bytes: int = Field(..., ge=0, description="Free filesystem disk bytes reported by agent")
    agent_version: str = Field(default="0.1.0", description="Provider agent version string")


class ProviderNodeStatusResponse(BaseModel):
    """
    Storage node status response for provider console.
    """
    configured: bool
    status: str
    storage_dir: Optional[str] = None
    quota_bytes: int
    quota_gb: float
    used_bytes: int
    available_bytes: int
    last_heartbeat: Optional[str] = None
    is_online: bool


class ProviderHeartbeatResponse(BaseModel):
    """
    Coordinator response to agent heartbeat.
    """
    status: str
    action: str = "CONTINUE"
    server_time: str


class FileRecordResponse(BaseModel):
    """
    Metadata of a consumer-owned file. Plaintext decryption keys are NEVER returned.
    """
    id: int
    filename: str
    file_size: int
    encrypted_size: int
    status: str
    created_at: str


class ChunkJobResponse(BaseModel):
    """
    Asynchronous chunk transfer job for a provider node.
    """
    id: int
    provider_node_id: int
    chunk_id: str
    file_id: int
    job_type: str
    status: str
    expires_at: str
    error_message: Optional[str] = None


class JobFailRequest(BaseModel):
    """
    Optional failure report payload from provider agent.
    """
    error_message: str = Field(default="Provider reported job failure", description="Failure reason")


class FileUploadRequest(BaseModel):
    """
    Consumer encrypted file upload request.
    Decryption keys are NEVER accepted, transmitted, or stored on coordinator.
    """
    model_config = ConfigDict(extra="forbid")

    filename: str = Field(..., description="Original client filename")
    file_size: int = Field(..., gt=0, description="Plaintext size in bytes")
    iv_hex: str = Field(..., description="12-byte initialization vector (24 hex characters)")
    sha256_hash: str = Field(..., description="SHA-256 hash of the ciphertext payload (64 hex characters)")
    ciphertext_base64: str = Field(..., description="Base64 encoded AES-256-GCM ciphertext payload")

    @field_validator("filename")
    @classmethod
    def validate_filename(cls, v: str) -> str:
        trimmed = Path(v.strip()).name
        if not trimmed:
            raise ValueError("Filename cannot be empty.")
        return trimmed

    @field_validator("iv_hex")
    @classmethod
    def validate_iv(cls, v: str) -> str:
        clean = v.strip().lower()
        if len(clean) != 24 or not all(c in "0123456789abcdef" for c in clean):
            raise ValueError("IV must be a 12-byte (24-character) hex string.")
        if clean == "0" * 24:
            raise ValueError("All-zero IV is insecure and rejected.")
        return clean

    @field_validator("sha256_hash")
    @classmethod
    def validate_sha256(cls, v: str) -> str:
        clean = v.strip().lower()
        if len(clean) != 64 or not all(c in "0123456789abcdef" for c in clean):
            raise ValueError("SHA-256 hash must be a 64-character hex string.")
        return clean


class FileUploadResponse(BaseModel):
    file_id: int
    filename: str
    file_size: int
    encrypted_size: int
    status: str
    provider_node_id: int
    job_id: int
    created_at: str


class FileStatusResponse(BaseModel):
    file_id: int
    filename: str
    file_size: int
    encrypted_size: int
    iv_hex: str
    sha256_hash: str
    status: str
    is_stored: bool
    created_at: str
    updated_at: Optional[str] = None


class FileDeleteResponse(BaseModel):
    file_id: int
    status: str
    message: str

