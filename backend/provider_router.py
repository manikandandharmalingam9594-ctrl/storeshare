from datetime import datetime, timezone
import hashlib
import json
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
    get_provider_job_telemetry,
    get_provider_node,
    get_provider_nodes_by_user_id,
    get_provider_reconciliation_records,
    poll_provider_jobs,
    record_provider_heartbeat,
    set_provider_node_status,
    update_chunk_verification,
    upsert_provider_node,
)
from backend.dependencies import require_provider
from backend.schemas import (
    ChunkInventoryItem,
    ChunkJobResponse,
    JobFailRequest,
    ProviderHeartbeatRequest,
    ProviderHeartbeatResponse,
    ProviderInventoryReportRequest,
    ProviderJobTelemetryResponse,
    ProviderNodeStatusResponse,
    ProviderSetupRequest,
    ReconciliationReportResponse,
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
    if raw_status in ("STOPPED", "PAUSED", "UNREGISTERED"):
        return raw_status

    last_hb = node.get("last_heartbeat")
    if not last_hb:
        return "OFFLINE"

    try:
        if isinstance(last_hb, str):
            hb_time = datetime.fromisoformat(last_hb.replace(" ", "T"))
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


def _generate_node_warnings(node: Dict[str, Any], effective_status: str) -> List[str]:
    """
    Generate actionable warnings for provider operators regarding disk space, quota, and state.
    """
    warnings: List[str] = []
    quota_bytes = node.get("quota_bytes", 0)
    used_bytes = node.get("used_bytes", 0)
    free_disk = node.get("free_disk_bytes", 0)

    if effective_status == "OFFLINE":
        warnings.append("Provider storage agent is OFFLINE. Start the agent on your machine to accept jobs.")
    elif effective_status == "STOPPED":
        warnings.append("Storage node is STOPPED by operator.")
    elif effective_status == "PAUSED":
        warnings.append("Storage node is PAUSED. Storage jobs will not be accepted.")

    if quota_bytes > 0:
        used_pct = (used_bytes / quota_bytes) * 100
        if used_pct >= 100:
            warnings.append("Storage quota exhausted (100% used). Increase quota to accept new chunks.")
        elif used_pct >= 90:
            warnings.append(f"Storage quota near capacity ({round(used_pct, 1)}% used).")

    if free_disk > 0 and free_disk < 1024 * 1024 * 1024:  # < 1 GB
        free_mb = round(free_disk / (1024**2), 1)
        warnings.append(f"Low physical disk space: host filesystem has only {free_mb} MB free remaining.")

    return warnings


@router.get("/nodes", summary="List All Nodes For Authenticated Provider")
def list_provider_nodes(
    provider_user: Dict[str, Any] = Depends(require_provider),
) -> List[Dict[str, Any]]:
    """
    List all storage nodes owned by the authenticated provider.
    """
    provider_id = provider_user["id"]
    nodes = get_provider_nodes_by_user_id(provider_id)
    for n in nodes:
        n["effective_status"] = _compute_effective_status(n)
        n["is_online"] = n["effective_status"] in ("ONLINE", "PAUSED")
    return nodes


@router.get("/node", response_model=ProviderNodeStatusResponse, summary="Get Provider Node Status")
def get_node_status(
    node_id: Optional[int] = None,
    provider_user: Dict[str, Any] = Depends(require_provider),
) -> ProviderNodeStatusResponse:
    """
    Retrieve live storage node status for the authenticated provider.
    Identity is strictly derived from validated JWT Bearer credentials.
    Supports optional node_id query param when an account owns multiple nodes.
    """
    provider_id = provider_user["id"]
    all_nodes = get_provider_nodes_by_user_id(provider_id)
    node = get_provider_node(provider_id, node_id=node_id)

    if not node:
        return ProviderNodeStatusResponse(
            node_id=None,
            configured=False,
            status="UNREGISTERED",
            storage_dir=None,
            quota_bytes=0,
            quota_gb=0.0,
            used_bytes=0,
            available_bytes=0,
            free_disk_bytes=0,
            warnings=["Storage directory not yet configured."],
            last_heartbeat=None,
            is_online=False,
            total_nodes=len(all_nodes),
            all_nodes=all_nodes,
        )

    effective_status = _compute_effective_status(node)
    quota_bytes = node.get("quota_bytes", 0)
    used_bytes = node.get("used_bytes", 0)
    free_disk_bytes = node.get("free_disk_bytes", 0)
    available = max(0, quota_bytes - used_bytes)
    quota_gb = round(quota_bytes / (1024**3), 2)
    warnings = _generate_node_warnings(node, effective_status)

    return ProviderNodeStatusResponse(
        node_id=node.get("id"),
        configured=bool(node.get("storage_dir") and quota_bytes > 0),
        status=effective_status,
        storage_dir=node.get("storage_dir"),
        computer_name=node.get("computer_name"),
        quota_bytes=quota_bytes,
        quota_gb=quota_gb,
        used_bytes=used_bytes,
        available_bytes=available,
        free_disk_bytes=free_disk_bytes,
        warnings=warnings,
        last_heartbeat=str(node["last_heartbeat"]) if node.get("last_heartbeat") else None,
        is_online=(effective_status in ("ONLINE", "PAUSED")),
        total_nodes=len(all_nodes),
        all_nodes=all_nodes,
    )


@router.post("/node/setup", response_model=ProviderNodeStatusResponse, summary="Configure Storage Directory and Quota")
def setup_node_storage(
    payload: ProviderSetupRequest,
    provider_user: Dict[str, Any] = Depends(require_provider),
) -> ProviderNodeStatusResponse:
    """
    Set or update local storage allocation and desired node run-state.
    Validates quota and updates database record for authenticated provider safely without deleting existing data.
    """
    provider_id = provider_user["id"]
    quota_bytes = int(payload.quota_gb * (1024**3))
    existing = get_provider_node(provider_id, node_id=payload.node_id)

    if payload.action == "STOP":
        target_status = "STOPPED"
        logger.info(f"Provider {provider_id} marked storage node as STOPPED.")
    elif payload.action == "PAUSE":
        target_status = "PAUSED"
        logger.info(f"Provider {provider_id} marked storage node as PAUSED.")
    elif payload.action == "START":
        # If resuming from PAUSED or STOPPED, transition back to ONLINE if recent heartbeat, else UNREGISTERED
        if existing and existing.get("status") in ("PAUSED", "STOPPED"):
            target_status = "ONLINE" if _compute_effective_status(existing) == "ONLINE" else "UNREGISTERED"
        elif existing and existing.get("status") in ("ONLINE", "OFFLINE"):
            target_status = existing["status"]
        else:
            target_status = "UNREGISTERED"
        logger.info(f"Provider {provider_id} started/resumed storage node.")
    else:  # UPDATE
        if existing and existing.get("status") in ("ONLINE", "OFFLINE", "PAUSED", "STOPPED"):
            target_status = existing["status"]
        else:
            target_status = "UNREGISTERED"

    node = upsert_provider_node(
        provider_id=provider_id,
        quota_bytes=quota_bytes,
        storage_dir=payload.storage_dir,
        status=target_status,
        computer_name=payload.computer_name,
    )

    effective_status = _compute_effective_status(node)
    available = max(0, node["quota_bytes"] - node["used_bytes"])
    warnings = _generate_node_warnings(node, effective_status)
    all_nodes = get_provider_nodes_by_user_id(provider_id)

    return ProviderNodeStatusResponse(
        node_id=node.get("id"),
        configured=True,
        status=effective_status,
        storage_dir=node["storage_dir"],
        computer_name=node.get("computer_name"),
        quota_bytes=node["quota_bytes"],
        quota_gb=round(node["quota_bytes"] / (1024**3), 2),
        used_bytes=node["used_bytes"],
        available_bytes=available,
        free_disk_bytes=node.get("free_disk_bytes", 0),
        warnings=warnings,
        last_heartbeat=str(node["last_heartbeat"]) if node.get("last_heartbeat") else None,
        is_online=(effective_status in ("ONLINE", "PAUSED")),
        total_nodes=len(all_nodes),
        all_nodes=all_nodes,
    )


@router.post("/node/heartbeat", response_model=ProviderHeartbeatResponse, summary="Agent Outbound Heartbeat")
def agent_heartbeat(
    payload: ProviderHeartbeatRequest,
    provider_user: Dict[str, Any] = Depends(require_provider),
) -> ProviderHeartbeatResponse:
    """
    Authenticated outbound heartbeat sent by the local Python provider agent.
    Updates last_heartbeat timestamp and capacity telemetry.
    Verifies physical presence and integrity of reported chunk inventory.
    """
    provider_id = provider_user["id"]
    existing = get_provider_node(provider_id)

    # Check operator-initiated state overrides
    if existing and existing.get("status") == "STOPPED":
        return ProviderHeartbeatResponse(
            status="STOPPED",
            action="STOP",
            quota_bytes=existing.get("quota_bytes"),
            storage_dir=existing.get("storage_dir"),
            server_time=datetime.now(timezone.utc).isoformat(),
        )

    if existing and existing.get("status") == "PAUSED":
        action = "PAUSE"
    else:
        action = "CONTINUE"

    inventory_json = None
    if payload.inventory:
        try:
            inventory_json = json.dumps(payload.inventory)
        except Exception as exc:
            logger.warning(f"Failed to serialize detailed inventory for provider {provider_id}: {exc}")

    # Record active heartbeat, computer hostname, and capacity metrics
    node = record_provider_heartbeat(
        provider_id=provider_id,
        used_bytes=payload.used_bytes,
        allocated_bytes=payload.allocated_bytes,
        computer_name=payload.computer_name,
        free_disk_bytes=payload.free_disk_bytes,
        inventory_json=inventory_json,
    )

    # If the agent reported chunk IDs, update verification timestamps
    if node and payload.chunk_ids:
        update_chunk_verification(node["id"], payload.chunk_ids)

    effective_status = _compute_effective_status(node) if node else "ONLINE"

    return ProviderHeartbeatResponse(
        status=effective_status,
        action=action,
        quota_bytes=node.get("quota_bytes") if node else None,
        storage_dir=node.get("storage_dir") if node else None,
        server_time=datetime.now(timezone.utc).isoformat(),
    )


@router.get("/reconciliation", response_model=ReconciliationReportResponse, summary="Provider Physical Chunk Reconciliation")
def get_reconciliation_report(
    node_id: Optional[int] = None,
    provider_user: Dict[str, Any] = Depends(require_provider),
) -> ReconciliationReportResponse:
    """
    Performs physical chunk reconciliation for the authenticated provider's storage node.
    Compares expected chunks with physical chunks on disk across 5 states:
    VERIFIED, MISSING, CORRUPTED, UNEXPECTED, UNVERIFIED.
    """
    provider_id = provider_user["id"]
    node = get_provider_node(provider_id, node_id=node_id)
    if not node:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Provider node not configured.")

    report = get_provider_reconciliation_records(provider_node_id=node["id"])
    return ReconciliationReportResponse(**report)


@router.post("/inventory", summary="Submit Detailed Physical Chunk Inventory")
def submit_inventory(
    payload: ProviderInventoryReportRequest,
    provider_user: Dict[str, Any] = Depends(require_provider),
) -> Dict[str, Any]:
    """
    Explicit physical inventory submission with chunk IDs, byte sizes, and SHA-256 hashes.
    """
    provider_id = provider_user["id"]
    node = get_provider_node(provider_id)
    if not node:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Provider node not configured.")

    items_list = [item.model_dump() for item in payload.items]
    inv_json = json.dumps(items_list)

    upsert_provider_node(
        provider_id=provider_id,
        quota_bytes=node["quota_bytes"],
        storage_dir=node["storage_dir"],
        status=node["status"],
        computer_name=node.get("computer_name"),
        free_disk_bytes=node.get("free_disk_bytes", 0),
        last_inventory=inv_json,
    )

    chunk_ids = [item.chunk_id for item in payload.items]
    if chunk_ids:
        update_chunk_verification(node["id"], chunk_ids)

    return {"status": "SUCCESS", "chunk_count": len(chunk_ids)}


@router.get("/jobs/history", response_model=ProviderJobTelemetryResponse, summary="Get Provider Job Telemetry and History")
def get_jobs_history(
    limit: int = 20,
    node_id: Optional[int] = None,
    provider_user: Dict[str, Any] = Depends(require_provider),
) -> ProviderJobTelemetryResponse:
    """
    Retrieve active, completed, and failed job counts and recent audit history.
    """
    provider_id = provider_user["id"]
    node = get_provider_node(provider_id, node_id=node_id)
    if not node:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Provider node not configured.")

    telemetry = get_provider_job_telemetry(provider_node_id=node["id"], limit=limit)
    return ProviderJobTelemetryResponse(**telemetry)


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
            chunk_size=j.get("chunk_size"),
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

    if job["status"] == "FAILED":
        return {
            "status": "FAILED",
            "job_id": job_id,
            "error_message": job.get("error_message"),
            "idempotent": True,
        }

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
