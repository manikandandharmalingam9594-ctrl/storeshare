"""
Configuration and settings parser for Store-Share Provider Agent.
Loads configuration from CLI flags, environment variables, or config files.
Never hardcodes credentials or secrets.
"""

from dataclasses import dataclass
import os
from typing import Optional


@dataclass
class AgentConfig:
    coordinator_url: str = "http://127.0.0.1:8001"
    storage_dir: Optional[str] = None
    quota_gb: float = 10.0
    token: Optional[str] = None
    email: Optional[str] = None
    password: Optional[str] = None
    heartbeat_interval: int = 15
    job_poll_interval: int = 2
    verify_tls: bool = True

    @classmethod
    def from_env(cls) -> "AgentConfig":
        """
        Populate default configuration values from environment variables if present.
        """
        coordinator_url = os.getenv("STORESHARE_COORDINATOR_URL", "http://127.0.0.1:8001")
        storage_dir = os.getenv("STORESHARE_STORAGE_DIR")
        quota_gb_str = os.getenv("STORESHARE_QUOTA_GB", "10.0")
        try:
            quota_gb = float(quota_gb_str)
        except ValueError:
            quota_gb = 10.0

        token = os.getenv("STORESHARE_AUTH_TOKEN")
        email = os.getenv("STORESHARE_EMAIL")
        password = os.getenv("STORESHARE_PASSWORD")
        heartbeat_str = os.getenv("STORESHARE_HEARTBEAT_INTERVAL", "15")
        try:
            heartbeat_interval = int(heartbeat_str)
        except ValueError:
            heartbeat_interval = 15

        job_poll_str = os.getenv("STORESHARE_JOB_POLL_INTERVAL", "2")
        try:
            job_poll_interval = int(job_poll_str)
        except ValueError:
            job_poll_interval = 2

        verify_tls_str = os.getenv("STORESHARE_VERIFY_TLS", "true").lower()
        verify_tls = verify_tls_str not in ("0", "false", "no")

        return cls(
            coordinator_url=coordinator_url,
            storage_dir=storage_dir,
            quota_gb=quota_gb,
            token=token,
            email=email,
            password=password,
            heartbeat_interval=heartbeat_interval,
            job_poll_interval=job_poll_interval,
            verify_tls=verify_tls,
        )
