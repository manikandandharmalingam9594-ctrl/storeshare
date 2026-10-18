from datetime import datetime, timezone
import hashlib
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from backend.database import (
    MAX_FILE_SIZE_BYTES,
    complete_chunk_job,
    expire_stale_jobs,
    fail_chunk_job,
    get_chunk_job_by_id,
    get_file_by_id,
    get_provider_node,
    poll_provider_jobs,
    record_provider_heartbeat,
    set_provider_node_status,
    upsert_provider_node,
)
from backend.dependencies import require_provider
from backend.schemas import (
    ChunkJobResponse,
    JobFailRequest,
    ProviderHeartbeatRequest,
    ProviderHeartbeatResponse,
    ProviderNodeStatusResponse,
    ProviderSetupRequest,
)
from backend.spool import get_spool_chunk, remove_spool_chunk, save_spool_chunk

logger = logging.getLogger("store-share-provider")
router = APIRouter(prefix="/api/provider", tags=["Provider Node"])

HEARTBEAT_TIMEOUT_SECONDS = 60


def _compute_effective_status(node: Dict[str, Any]) -> str:
    """
    Compute real-time status based on heartbeat freshness.
    If coordinator hasn't received a heartbeat within timeout, mark as OFFLINE.
    """
    raw_status = node.get("status", "UNREGISTERED")
    if raw_status == "STOPPED":
        return "STOPPED"
    if raw_status == "UNREGISTERED":
        return "UNREGISTERED"

    last_hb = node.get("last_heartbeat")
    if not last_hb:
        return "OFFLINE"

    try:
        # SQLite stores timestamps as 'YYYY-MM-DD HH:MM:SS'
        if isinstance(last_hb, str):
            # Parse SQLite timestamp
            hb_time = datetime.fromisoformat(last_hb)
        else:
            hb_time = last_hb

        if hb_time.tzinfo is None:
            hb_time = hb_time.replace(tzinfo=timezone.utc)

        age_seconds = (datetime.now(timezone.utc) - hb_time).total_seconds()
        if age_seconds > HEARTBEAT_TIMEOUT_SECONDS:
            return "OFFLINE"
        return "ONLINE"
    except Exception as exc:
        logger.warning(f"Error parsing last_heartbeat timestamp '{last_hb}': {exc}")
        return "OFFLINE"


@router.get("/node", response_model=ProviderNodeStatusResponse, summary="Get Provider Node Status")
def get_node_status(
    provider_user: Dict[str, Any] = Depends(require_provider),
) -> ProviderNodeStatusResponse:
    """
    Retrieve live storage node status for the authenticated provider.
    Identity is strictly derived from validated JWT Bearer credentials.
    """
    provider_id = provider_user["id"]
    node = get_provider_node(provider_id)

    if not node:
        return ProviderNodeStatusResponse(
            configured=False,
            status="UNREGISTERED",
            storage_dir=None,
            quota_bytes=0,
            quota_gb=0.0,
            used_bytes=0,
            available_bytes=0,
            last_heartbeat=None,
            is_online=False,
        )

    effective_status = _compute_effective_status(node)
    quota_bytes = node.get("quota_bytes", 0)
    used_bytes = node.get("used_bytes", 0)
    available = max(0, quota_bytes - used_bytes)
    quota_gb = round(quota_bytes / (1024**3), 2)

    return ProviderNodeStatusResponse(
        configured=bool(node.get("storage_dir") and quota_bytes > 0),
        status=effective_status,
        storage_dir=node.get("storage_dir"),
        quota_bytes=quota_bytes,
        quota_gb=quota_gb,
        used_bytes=used_bytes,
        available_bytes=available,
        last_heartbeat=str(node["last_heartbeat"]) if node.get("last_heartbeat") else None,
        is_online=(effective_status == "ONLINE"),
    )


@router.post("/node/setup", response_model=ProviderNodeStatusResponse, summary="Configure Storage Directory and Quota")
def setup_node_storage(
    payload: ProviderSetupRequest,
    provider_user: Dict[str, Any] = Depends(require_provider),
) -> ProviderNodeStatusResponse:
    """
    Set or update local storage allocation and desired node run-state.
    Validates quota and updates database record for authenticated provider.
    """
    provider_id = provider_user["id"]
    quota_bytes = int(payload.quota_gb * (1024**3))
    existing = get_provider_node(provider_id)

    if payload.action == "STOP":
        target_status = "STOPPED"
        logger.info(f"Provider {provider_id} marked storage node as STOPPED.")
    else:
        # If starting or updating, keep existing status or default to UNREGISTERED/OFFLINE
        if existing and existing.get("status") in ("ONLINE", "OFFLINE"):
            target_status = existing["status"]
        else:
            target_status = "UNREGISTERED"
        logger.info(
            f"Provider {provider_id} configured storage: dir='{payload.storage_dir}', "
            f"quota={payload.quota_gb} GB ({quota_bytes} bytes), action={payload.action}."
        )

    node = upsert_provider_node(
        provider_id=provider_id,
        quota_bytes=quota_bytes,
        storage_dir=payload.storage_dir,
        status=target_status,
    )

    effective_status = _compute_effective_status(node)
    available = max(0, node["quota_bytes"] - node["used_bytes"])

    return ProviderNodeStatusResponse(
        configured=True,
        status=effective_status,
        storage_dir=node["storage_dir"],
        quota_bytes=node["quota_bytes"],
        quota_gb=round(node["quota_bytes"] / (1024**3), 2),
        used_bytes=node["used_bytes"],
        available_bytes=available,
        last_heartbeat=str(node["last_heartbeat"]) if node.get("last_heartbeat") else None,
        is_online=(effective_status == "ONLINE"),
    )


@router.post("/node/heartbeat", response_model=ProviderHeartbeatResponse, summary="Agent Outbound Heartbeat")
def agent_heartbeat(
    payload: ProviderHeartbeatRequest,
    provider_user: Dict[str, Any] = Depends(require_provider),
) -> ProviderHeartbeatResponse:
    """
    Authenticated outbound heartbeat sent by the local Python provider agent.
    Updates last_heartbeat timestamp and transitions node to ONLINE.
    """
    provider_id = provider_user["id"]
    existing = get_provider_node(provider_id)

    # If the provider explicitly clicked 'Stop Storage Node', notify the agent to pause/stop
    if existing and existing.get("status") == "STOPPED":
        return ProviderHeartbeatResponse(
            status="STOPPED",
            action="STOP",
            server_time=datetime.now(timezone.utc).isoformat(),
        )

    # Record active heartbeat and update capacity metrics
    record_provider_heartbeat(
        provider_id=provider_id,
        used_bytes=payload.used_bytes,
        allocated_bytes=payload.allocated_bytes,
    )

    return ProviderHeartbeatResponse(
        status="ONLINE",
        action="CONTINUE",
        server_time=datetime.now(timezone.utc).isoformat(),
    )


# ==============================================================================
# Provider Asynchronous Job Transfer Pipeline (NAT-Friendly Outbound Polling)
# ==============================================================================

@router.get("/jobs", response_model=List[ChunkJobResponse], summary="Poll Pending Storage/Retrieval Jobs")
def get_pending_jobs(
    limit: int = 5,
    provider_user: Dict[str, Any] = Depends(require_provider),
) -> List[ChunkJobResponse]:
    """
    Authenticated provider polls for asynchronous chunk jobs assigned to its node.
    Enforces strict provider isolation: only jobs assigned to authenticated node are returned.
    """
    provider_id = provider_user["id"]
    node = get_provider_node(provider_id)
    if not node:
        return []

    # Clean up stale expired jobs first
    expire_stale_jobs()

    # Poll jobs assigned to this specific node (transitions to PROCESSING)
    jobs = poll_provider_jobs(provider_node_id=node["id"], limit=limit)
    return [
        ChunkJobResponse(
            id=j["id"],
            provider_node_id=j["provider_node_id"],
            chunk_id=j["chunk_id"],
            file_id=j["file_id"],
            job_type=j["job_type"],
            status=j["status"],
            expires_at=str(j["expires_at"]),
            error_message=j.get("error_message"),
        )
        for j in jobs
    ]


@router.get("/jobs/{job_id}/data", summary="Download Ephemeral Chunk Payload for STORE_CHUNK Job")
def get_job_data(
    job_id: int,
    provider_user: Dict[str, Any] = Depends(require_provider),
) -> Response:
    """
    Provider fetches ephemeral ciphertext from coordinator to store on local disk.
    Strictly verifies node ownership and active job status.
    """
    provider_id = provider_user["id"]
    node = get_provider_node(provider_id)
    if not node:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Provider node not configured.")

    job = get_chunk_job_by_id(job_id)
    if not job:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found.")

    # Strict isolation: verify job belongs to the authenticated provider's node
    if job["provider_node_id"] != node["id"]:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Access denied: job is assigned to another provider.")

    if job["job_type"] != "STORE_CHUNK":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Job type does not have coordinator data to download.")

    if job["status"] not in ("QUEUED", "PROCESSING"):
        raise HTTPException(status.HTTP_409_CONFLICT, f"Job cannot be fetched in state '{job['status']}'.")

    data = get_spool_chunk(job["chunk_id"])
    if data is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Transient chunk payload expired or not found in coordinator spool.")

    file_rec = get_file_by_id(job["file_id"])
    sha_checksum = file_rec["sha256_hash"] if file_rec else hashlib.sha256(data).hexdigest()

    return Response(
        content=data,
        media_type="application/octet-stream",
        headers={
            "X-Chunk-ID": job["chunk_id"],
            "X-Chunk-Size": str(len(data)),
            "X-SHA256-Checksum": sha_checksum,
        },
    )


@router.post("/jobs/{job_id}/data", summary="Upload Ciphertext Payload from Local Disk for RETRIEVE_CHUNK Job")
async def post_job_data(
    job_id: int,
    request: Request,
    provider_user: Dict[str, Any] = Depends(require_provider),
) -> Dict[str, Any]:
    """
    Provider uploads chunk payload read from its local disk for download handoff.
    Verifies checksum integrity and bounds transfer size (<= 5 MB).
    """
    provider_id = provider_user["id"]
    node = get_provider_node(provider_id)
    if not node:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Provider node not configured.")

    job = get_chunk_job_by_id(job_id)
    if not job:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found.")

    if job["provider_node_id"] != node["id"]:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Access denied: job is assigned to another provider.")

    if job["job_type"] != "RETRIEVE_CHUNK":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Job type does not accept uploaded data.")

    if job["status"] not in ("QUEUED", "PROCESSING"):
        raise HTTPException(status.HTTP_409_CONFLICT, f"Job is in state '{job['status']}', cannot receive data.")

    data = await request.body()
    if len(data) == 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Cannot upload empty chunk payload.")

    if len(data) > MAX_FILE_SIZE_BYTES:
        raise HTTPException(
            status.HTTP_413_CONTENT_TOO_LARGE if hasattr(status, "HTTP_413_CONTENT_TOO_LARGE") else 413,
            f"Chunk payload exceeds maximum allowed size of {MAX_FILE_SIZE_BYTES} bytes.",
        )

    # Verify SHA-256 integrity against file record
    computed_hash = hashlib.sha256(data).hexdigest()
    file_rec = get_file_by_id(job["file_id"])
    if file_rec and file_rec["sha256_hash"] != computed_hash:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Integrity check failed: computed SHA-256 '{computed_hash}' does not match expected '{file_rec['sha256_hash']}'.",
        )

    # Save to coordinator ephemeral spool
    save_spool_chunk(job["chunk_id"], data)
    return {
        "status": "DATA_RECEIVED",
        "job_id": job_id,
        "bytes_received": len(data),
        "sha256_hash": computed_hash,
    }


@router.post("/jobs/{job_id}/complete", summary="Acknowledge Successful Job Completion")
def complete_job_endpoint(
    job_id: int,
    provider_user: Dict[str, Any] = Depends(require_provider),
) -> Dict[str, Any]:
    """
    Provider confirms successful disk operation.
    Idempotent: repeating complete on an already completed job succeeds.
    Purges transient spool buffer immediately on store completion.
    """
    provider_id = provider_user["id"]
    node = get_provider_node(provider_id)
    if not node:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Provider node not configured.")

    job = get_chunk_job_by_id(job_id)
    if not job:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found.")

    if job["provider_node_id"] != node["id"]:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Access denied: job is assigned to another provider.")

    if job["status"] == "COMPLETED":
        return {"status": "COMPLETED", "job_id": job_id, "idempotent": True}

    if job["status"] in ("FAILED", "TIMED_OUT"):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Cannot complete job: status has already reached terminal state '{job['status']}'.",
        )

    success = complete_chunk_job(job_id=job_id, provider_node_id=node["id"])
    if not success:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Failed to complete job record.")

    # Immediately purge transient spool if this was a STORE_CHUNK job
    if job["job_type"] == "STORE_CHUNK":
        remove_spool_chunk(job["chunk_id"])

    return {"status": "COMPLETED", "job_id": job_id}


@router.post("/jobs/{job_id}/fail", summary="Report Job Execution Failure")
def fail_job_endpoint(
    job_id: int,
    payload: JobFailRequest = JobFailRequest(),
    provider_user: Dict[str, Any] = Depends(require_provider),
) -> Dict[str, Any]:
    """
    Provider reports local IO or quota error.
    Marks job and associated file/chunk as FAILED and releases reserved quota.
    Purges transient spool buffer immediately.
    """
    provider_id = provider_user["id"]
    node = get_provider_node(provider_id)
    if not node:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Provider node not configured.")

    job = get_chunk_job_by_id(job_id)
    if not job:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found.")

    if job["provider_node_id"] != node["id"]:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Access denied: job is assigned to another provider.")

    if job["status"] == "COMPLETED":
        raise HTTPException(status.HTTP_409_CONFLICT, "Cannot fail an already completed job.")

    success = fail_chunk_job(
        job_id=job_id,
        provider_node_id=node["id"],
        error_message=payload.error_message,
    )
    if not success:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Failed to update job status to FAILED.")

    # Purge any transient spool buffer
    remove_spool_chunk(job["chunk_id"])

    return {
        "status": "FAILED",
        "job_id": job_id,
        "error_message": payload.error_message,
    }
