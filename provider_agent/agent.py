"""
Store-Share Local Provider Agent.

A standalone Python process that runs on the storage provider's machine.
Features:
1. Designed for providers behind NAT using outbound-only HTTP/HTTPS connections.
2. Validates and manages local storage directory and quota via StorageManager.
3. Authenticates with coordinator via JWT Bearer token or provider credentials.
4. Sends periodic verified telemetry heartbeats to coordinator.
5. Strictly obeys coordinator actions (e.g. STOP command).
6. TLS certificate validation enabled by default (verify=True).
"""

from datetime import datetime, timezone
import logging
import signal
import socket
import sys
import time
from typing import Any, Dict, List, Optional

import httpx

from provider_agent.config import AgentConfig
from provider_agent.storage import StorageManager

logger = logging.getLogger("store-share-agent")


class ProviderAgent:
    """
    Local Provider Agent Daemon.
    """

    def __init__(
        self,
        config: AgentConfig,
        storage_manager: Optional[StorageManager] = None,
    ) -> None:
        self.config = config
        self.coordinator_url = config.coordinator_url.rstrip("/")
        self.token = config.token
        self.provider_id: Optional[int] = None
        self.provider_email: Optional[str] = None
        self._running = False
        self._is_paused = False

        # Configure HTTP client with TLS verification and timeout
        # Providers behind NAT communicate strictly via outbound calls.
        self.client = httpx.Client(
            base_url=self.coordinator_url,
            verify=config.verify_tls,
            timeout=15.0,
        )

        # Initialize local storage manager
        if storage_manager:
            self.storage = storage_manager
        else:
            if not config.storage_dir:
                raise ValueError("Storage directory must be specified in configuration or CLI arguments.")
            quota_bytes = int(config.quota_gb * (1024**3))
            self.storage = StorageManager(
                storage_dir=config.storage_dir,
                quota_bytes=quota_bytes,
                create_if_missing=True,
            )

    def close(self) -> None:
        """Close HTTP client session."""
        try:
            self.client.close()
        except Exception:
            pass

    def __enter__(self) -> "ProviderAgent":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def _get_auth_headers(self) -> Dict[str, str]:
        if not self.token:
            raise RuntimeError("Agent is not authenticated. Call authenticate() first.")
        return {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }

    def authenticate(self) -> Dict[str, Any]:
        """
        Authenticate with the coordinator using Bearer token or email/password.
        Derives provider identity strictly from the server's validated response.
        Never trusts client-supplied user IDs or roles.
        """
        # If token is provided, verify it via GET /api/auth/me
        if self.token:
            logger.info("Validating existing Bearer token with coordinator...")
            try:
                res = self.client.get(
                    "/api/auth/me",
                    headers={"Authorization": f"Bearer {self.token}"},
                )
            except Exception as exc:
                raise RuntimeError(f"Failed to connect to coordinator at {self.coordinator_url}: {exc}") from exc

            if res.status_code == 200:
                user_data = res.json()
                if user_data.get("role") != "PROVIDER":
                    raise PermissionError(
                        f"Account {user_data.get('email')} has role '{user_data.get('role')}', "
                        "but only 'PROVIDER' accounts can run a storage node."
                    )

                self.provider_id = user_data["id"]
                self.provider_email = user_data["email"]
                logger.info(f"Authenticated as Provider #{self.provider_id} ({self.provider_email}).")
                return user_data

            # If token was rejected (e.g. 401 expired), clear it and fall back to email/password if available
            logger.warning(f"Existing token rejected by coordinator (HTTP {res.status_code}). Clearing invalid token.")
            self.token = None
            if not (self.config.email and self.config.password):
                raise RuntimeError(f"Authentication failed: token rejected by coordinator (HTTP {res.status_code}).")

        # If email & password are provided, log in via POST /api/auth/login
        if self.config.email and self.config.password:
            logger.info(f"Authenticating provider '{self.config.email}' with coordinator...")
            try:
                res = self.client.post(
                    "/api/auth/login",
                    json={"email": self.config.email, "password": self.config.password},
                )
            except Exception as exc:
                raise RuntimeError(f"Failed to connect to coordinator at {self.coordinator_url}: {exc}") from exc

            if res.status_code != 200:
                detail = res.text
                try:
                    detail = res.json().get("detail", detail)
                except Exception:
                    pass
                raise RuntimeError(f"Login failed (HTTP {res.status_code}): {detail}")

            data = res.json()
            token = data.get("access_token")
            user = data.get("user", {})
            if user.get("role") != "PROVIDER":
                raise PermissionError(
                    f"Account {user.get('email')} has role '{user.get('role')}', "
                    "but only 'PROVIDER' accounts can run a storage node."
                )

            self.token = token
            self.provider_id = user["id"]
            self.provider_email = user["email"]
            logger.info(f"Login successful: Provider #{self.provider_id} ({self.provider_email}).")
            return user

        raise ValueError("No authentication credentials provided. Provide --token or --email and --password.")

    def register_storage(self, action: str = "START") -> Dict[str, Any]:
        """
        Notify coordinator of the local storage directory and quota configuration.
        """
        if not self.token:
            self.authenticate()

        stats = self.storage.get_stats()
        payload = {
            "storage_dir": stats["storage_dir"],
            "quota_gb": stats["quota_gb"],
            "action": action,
            "computer_name": socket.gethostname(),
        }

        logger.info(f"Registering storage node setup with coordinator (action={action})...")
        res = self.client.post(
            "/api/provider/node/setup",
            headers=self._get_auth_headers(),
            json=payload,
        )

        if res.status_code == 401 and (self.config.email and self.config.password):
            logger.warning("Node setup returned HTTP 401; re-authenticating...")
            self.token = None
            self.authenticate()
            res = self.client.post(
                "/api/provider/node/setup",
                headers=self._get_auth_headers(),
                json=payload,
            )

        if res.status_code != 200:
            raise RuntimeError(f"Failed to register storage setup (HTTP {res.status_code}): {res.text}")

        data = res.json()
        logger.info(f"Node setup response: status={data.get('status')}, quota={data.get('quota_gb')} GB.")
        return data

    def send_heartbeat(self) -> Dict[str, Any]:
        """
        Send outbound telemetry heartbeat to coordinator.
        Reports allocated bytes, used bytes, physical free disk bytes, computer hostname, and chunk inventory.
        """
        if not self.token:
            self.authenticate()

        stats = self.storage.get_stats()
        chunk_ids = self.storage.get_chunk_inventory()
        detailed_inventory = self.storage.get_detailed_inventory()
        payload = {
            "allocated_bytes": stats["quota_bytes"],
            "used_bytes": stats["used_bytes"],
            "free_disk_bytes": stats["free_disk_bytes"],
            "agent_version": "0.1.0",
            "computer_name": socket.gethostname(),
            "chunk_ids": chunk_ids,
            "inventory": detailed_inventory,
        }

        res = self.client.post(
            "/api/provider/node/heartbeat",
            headers=self._get_auth_headers(),
            json=payload,
        )

        if res.status_code == 401 and (self.config.email and self.config.password):
            logger.warning("Heartbeat returned HTTP 401; re-authenticating...")
            self.token = None
            self.authenticate()
            res = self.client.post(
                "/api/provider/node/heartbeat",
                headers=self._get_auth_headers(),
                json=payload,
            )

        if res.status_code != 200:
            raise RuntimeError(f"Heartbeat failed (HTTP {res.status_code}): {res.text}")

        data = res.json()

        # Dynamic coordinator quota synchronization without deleting data
        server_quota = data.get("quota_bytes")
        if server_quota and server_quota > 0 and server_quota != self.storage.quota_bytes:
            logger.info(f"Coordinator updated quota to {server_quota} bytes. Syncing local storage manager...")
            self.storage.set_quota(server_quota)

        server_action = data.get("action")
        node_status = data.get("status")
        if server_action in ("STOP", "PAUSE") or node_status in ("STOPPED", "PAUSED"):
            self._is_paused = True
        elif server_action == "CONTINUE" and node_status == "ONLINE" and self._is_paused:
            self._is_paused = False

        return data

    def heartbeat_once(self) -> Dict[str, Any]:
        """
        Performs authentication, initial setup sync, and a single heartbeat.
        Useful for scripted health checks or integration testing.
        """
        if not self.token or not self.provider_id:
            self.authenticate()
        self.register_storage(action="START")
        return self.send_heartbeat()

    def poll_jobs(self, limit: int = 5) -> List[Dict[str, Any]]:
        """
        Poll coordinator for asynchronous chunk transfer jobs assigned to this node.
        """
        if not self.token:
            self.authenticate()

        try:
            res = self.client.get(
                "/api/provider/jobs",
                headers=self._get_auth_headers(),
                params={"limit": limit},
            )
        except Exception as exc:
            logger.warning(f"Failed to poll jobs from coordinator: {exc}")
            return []

        if res.status_code == 200:
            return res.json()
        elif res.status_code == 401:
            logger.warning("Token expired or unauthorized during job poll; re-authenticating...")
            try:
                self.token = None
                self.authenticate()
                res = self.client.get(
                    "/api/provider/jobs",
                    headers=self._get_auth_headers(),
                    params={"limit": limit},
                )
                if res.status_code == 200:
                    return res.json()
            except Exception as exc:
                logger.error(f"Re-authentication failed: {exc}")
            return []
        else:
            logger.warning(f"Job poll returned unexpected status {res.status_code}: {res.text}")
            return []

    def fetch_job_data(self, job_id: int) -> bytes:
        """
        Download transient chunk payload from coordinator for a STORE_CHUNK job.
        """
        if not self.token:
            self.authenticate()

        res = self.client.get(
            f"/api/provider/jobs/{job_id}/data",
            headers={"Authorization": f"Bearer {self.token}"},
        )
        if res.status_code == 401 and (self.config.email and self.config.password):
            self.token = None
            self.authenticate()
            res = self.client.get(
                f"/api/provider/jobs/{job_id}/data",
                headers={"Authorization": f"Bearer {self.token}"},
            )
        if res.status_code != 200:
            raise RuntimeError(f"Failed to download job data (HTTP {res.status_code}): {res.text}")
        return res.content

    def upload_job_data(self, job_id: int, data: bytes) -> Dict[str, Any]:
        """
        Upload chunk payload read from local storage to coordinator for a RETRIEVE_CHUNK job.
        """
        if not self.token:
            self.authenticate()

        res = self.client.post(
            f"/api/provider/jobs/{job_id}/data",
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/octet-stream",
            },
            content=data,
        )
        if res.status_code == 401 and (self.config.email and self.config.password):
            self.token = None
            self.authenticate()
            res = self.client.post(
                f"/api/provider/jobs/{job_id}/data",
                headers={
                    "Authorization": f"Bearer {self.token}",
                    "Content-Type": "application/octet-stream",
                },
                content=data,
            )
        if res.status_code != 200:
            raise RuntimeError(f"Failed to upload retrieved chunk data (HTTP {res.status_code}): {res.text}")
        return res.json()

    def complete_job(self, job_id: int) -> Dict[str, Any]:
        """
        Acknowledge successful execution of a chunk job.
        """
        if not self.token:
            self.authenticate()

        res = self.client.post(
            f"/api/provider/jobs/{job_id}/complete",
            headers=self._get_auth_headers(),
        )
        if res.status_code == 401 and (self.config.email and self.config.password):
            logger.warning("complete_job returned HTTP 401; re-authenticating...")
            self.token = None
            self.authenticate()
            res = self.client.post(
                f"/api/provider/jobs/{job_id}/complete",
                headers=self._get_auth_headers(),
            )
        if res.status_code != 200:
            raise RuntimeError(f"Failed to acknowledge job completion (HTTP {res.status_code}): {res.text}")
        return res.json()

    def fail_job(self, job_id: int, error_message: str) -> Dict[str, Any]:
        """
        Report failure of a chunk job to coordinator.
        """
        if not self.token:
            self.authenticate()

        try:
            res = self.client.post(
                f"/api/provider/jobs/{job_id}/fail",
                headers=self._get_auth_headers(),
                json={"error_message": error_message},
            )
            if res.status_code == 401 and (self.config.email and self.config.password):
                logger.warning("fail_job returned HTTP 401; re-authenticating...")
                self.token = None
                self.authenticate()
                res = self.client.post(
                    f"/api/provider/jobs/{job_id}/fail",
                    headers=self._get_auth_headers(),
                    json={"error_message": error_message},
                )
            if res.status_code != 200:
                logger.warning(f"Failed to report job failure to coordinator (HTTP {res.status_code}): {res.text}")
                return {}
            return res.json()
        except Exception as exc:
            logger.error(f"Error reporting job failure: {exc}")
            return {}

    def process_single_job(self, job: Dict[str, Any]) -> bool:
        """
        Execute an asynchronous chunk transfer job using StorageManager.
        Preserves atomic writes, safe chunk IDs, and quota limits.
        """
        job_id = job["id"]
        job_type = job.get("job_type")
        chunk_id = job.get("chunk_id")

        if not chunk_id:
            logger.error(f"Job #{job_id} missing chunk_id.")
            self.fail_job(job_id, "Missing chunk_id in job payload")
            return False

        logger.info(f"Processing job #{job_id} ({job_type}) for chunk '{chunk_id}'...")

        if self._is_paused and job_type == "STORE_CHUNK":
            msg = "Storage node is PAUSED; rejecting new STORE_CHUNK job."
            logger.warning(f"Job #{job_id}: {msg}")
            self.fail_job(job_id, msg)
            return False

        try:
            if job_type == "STORE_CHUNK":
                # 1. Fetch ciphertext from coordinator
                ciphertext = self.fetch_job_data(job_id)
                # 2. Write to local storage atomically
                written_bytes = self.storage.write_chunk_atomic(chunk_id, ciphertext)
                logger.info(f"Job #{job_id}: Atomically wrote {written_bytes} bytes for chunk '{chunk_id}'.")
                # 3. Acknowledge success to coordinator
                self.complete_job(job_id)
                logger.info(f"Job #{job_id} (STORE_CHUNK) successfully completed.")
                return True

            elif job_type == "RETRIEVE_CHUNK":
                # 1. Read chunk from local disk
                ciphertext = self.storage.read_chunk(chunk_id)
                logger.info(f"Job #{job_id}: Read {len(ciphertext)} bytes from local storage for chunk '{chunk_id}'.")
                # 2. Upload to coordinator
                self.upload_job_data(job_id, ciphertext)
                # 3. Acknowledge success
                self.complete_job(job_id)
                logger.info(f"Job #{job_id} (RETRIEVE_CHUNK) successfully completed.")
                return True

            elif job_type == "DELETE_CHUNK":
                # 1. Safely remove chunk from disk and adjust internal quota
                deleted = self.storage.delete_chunk(chunk_id)
                logger.info(f"Job #{job_id}: Deleted chunk '{chunk_id}' from disk (existed={deleted}).")
                # 2. Acknowledge success
                self.complete_job(job_id)
                logger.info(f"Job #{job_id} (DELETE_CHUNK) successfully completed.")
                return True

            else:
                msg = f"Unsupported job type: {job_type}"
                logger.error(f"Job #{job_id}: {msg}")
                self.fail_job(job_id, msg)
                return False

        except Exception as exc:
            err_msg = f"Job #{job_id} failed: {exc}"
            logger.error(err_msg)
            self.fail_job(job_id, str(exc))
            return False

    def poll_and_process_jobs_once(self, limit: int = 5) -> int:
        """
        Poll coordinator for pending jobs and process them sequentially.
        Returns the number of jobs processed.
        """
        jobs = self.poll_jobs(limit=limit)
        if not jobs:
            return 0

        logger.info(f"Received {len(jobs)} pending jobs from coordinator.")
        processed_count = 0
        for job in jobs:
            if self._is_paused:
                break
            success = self.process_single_job(job)
            if success:
                processed_count += 1

        return processed_count

    def run(self, max_iterations: Optional[int] = None) -> None:
        """
        Start the agent heartbeat and asynchronous job processing loop.
        Runs indefinitely until stopped or interrupted.
        """
        logger.info("Starting Store-Share Provider Agent daemon...")
        self.authenticate()
        self.register_storage(action="START")

        self._running = True
        iterations = 0

        # Setup graceful termination handlers
        def _handle_exit(sig, frame):
            logger.info("Shutdown signal received. Stopping provider agent...")
            self._running = False

        try:
            signal.signal(signal.SIGINT, _handle_exit)
            signal.signal(signal.SIGTERM, _handle_exit)
        except Exception:
            pass

        logger.info(
            f"Provider Agent active. Storage: {self.storage.storage_dir} | "
            f"Quota: {self.config.quota_gb} GB | Heartbeat: {self.config.heartbeat_interval}s | "
            f"Job Poll: {self.config.job_poll_interval}s"
        )

        last_heartbeat_time = 0.0
        last_job_poll_time = 0.0

        while self._running:
            now = time.time()

            # 1. Periodic heartbeat transmission
            if now - last_heartbeat_time >= self.config.heartbeat_interval:
                try:
                    iterations += 1
                    response = self.send_heartbeat()
                    server_action = response.get("action", "CONTINUE")
                    node_status = response.get("status", "ONLINE")

                    if server_action in ("STOP", "PAUSE") or node_status in ("STOPPED", "PAUSED"):
                        logger.warning(
                            f"Coordinator signaled {server_action} (status={node_status}). Storage node paused/stopped by operator."
                        )
                        self._is_paused = True
                    else:
                        if self._is_paused:
                            logger.info("Coordinator signaled resume. Resuming normal operations.")
                            self._is_paused = False

                        stats = self.storage.get_stats()
                        logger.info(
                            f"[Heartbeat #{iterations}] Status: {node_status} | "
                            f"Used: {stats['used_mb']} MB / {stats['quota_gb']} GB | "
                            f"Chunks: {stats['chunk_count']}"
                        )
                    last_heartbeat_time = now

                    if max_iterations is not None and iterations >= max_iterations:
                        logger.info(f"Reached max iterations ({max_iterations}). Exiting loop.")
                        break

                except Exception as exc:
                    logger.error(f"Error during heartbeat transmission: {exc}")
                    last_heartbeat_time = now

            # 2. Continuous asynchronous job polling (when not paused)
            if not self._is_paused and (now - last_job_poll_time >= self.config.job_poll_interval):
                try:
                    processed = self.poll_and_process_jobs_once()
                    last_job_poll_time = time.time()
                    if processed > 0:
                        # Process additional queued jobs immediately
                        continue
                except Exception as exc:
                    logger.error(f"Error during job polling/processing: {exc}")
                    last_job_poll_time = time.time()

            # Responsive sleep tick
            time.sleep(0.5)

        logger.info("Provider Agent daemon has stopped.")
