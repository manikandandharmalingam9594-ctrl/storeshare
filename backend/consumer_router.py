"""
Consumer Files API Router for Store-Share.

Implements Phase 3: Consumer Upload/Download and File Management APIs.
Decryption keys and plaintext consumer data NEVER touch the coordinator or providers.

Endpoints:
1. POST   /api/consumer/files/upload       - Upload ciphertext, reserve quota, queue STORE_CHUNK
2. GET    /api/consumer/files              - List authenticated consumer's active files
3. GET    /api/consumer/files/{id}/status  - Live file and transfer status
4. GET    /api/consumer/files/{id}/download- Trigger RETRIEVE_CHUNK, verify SHA-256, deliver ciphertext
5. DELETE /api/consumer/files/{id}         - Trigger DELETE_CHUNK, release quota, mark DELETED
"""

import asyncio
import base64
import hashlib
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from backend.database import (
    MAX_FILE_SIZE_BYTES,
    cancel_pending_chunk_jobs_for_file,
    compute_node_effective_status,
    create_chunk_job,
    create_file_chunk,
    create_file_record,
    delete_file_chunks_for_file,
    expire_stale_jobs,
    find_eligible_provider_node,
    get_active_chunk_job,
    get_chunk_job_by_id,
    get_file_by_id,
    get_file_chunks,
    get_provider_node_by_id,
    list_consumer_files,
    release_provider_quota,
    reserve_provider_quota,
    update_file_status,
)
from backend.dependencies import require_consumer
from backend.schemas import (
    FileDeleteResponse,
    FileStatusResponse,
    FileUploadRequest,
    FileUploadResponse,
)
from backend.spool import get_spool_chunk, remove_spool_chunk, save_spool_chunk

logger = logging.getLogger("store-share-consumer")
router = APIRouter(prefix="/api/consumer/files", tags=["Consumer Files"])


def _validate_ciphertext_cryptography(
    file_size: int,
    ciphertext_bytes: bytes,
    iv_hex: str,
    sha256_hash: str,
) -> None:
    """
    Strict cryptographic parameter validation for AES-256-GCM ciphertext.
    Never accepts arbitrary unencrypted plaintext or forged tags.
    """
    if file_size <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File size must be greater than 0 bytes.",
        )

    if file_size > MAX_FILE_SIZE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE if hasattr(status, "HTTP_413_CONTENT_TOO_LARGE") else 413,
            detail=f"Plaintext file size ({file_size} bytes) exceeds maximum allowable limit of 5 MB ({MAX_FILE_SIZE_BYTES} bytes).",
        )

    # In AES-256-GCM, ciphertext size is strictly: plaintext_size + 16-byte authentication tag
    expected_enc_size = file_size + 16
    if len(ciphertext_bytes) != expected_enc_size:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Invalid ciphertext payload length: expected {expected_enc_size} bytes "
                f"(plaintext {file_size} bytes + 16-byte GCM authentication tag), "
                f"received {len(ciphertext_bytes)} bytes."
            ),
        )

    clean_iv = iv_hex.strip().lower()
    if len(clean_iv) != 24 or not all(c in "0123456789abcdef" for c in clean_iv):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid IV: must be a 12-byte (24-character) hex string.",
        )

    if clean_iv == "0" * 24:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Insecure IV rejected: static all-zero initialization vector is prohibited.",
        )

    # Verify SHA-256 checksum against received ciphertext bytes
    computed_sha = hashlib.sha256(ciphertext_bytes).hexdigest()
    if computed_sha != sha256_hash.strip().lower():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Ciphertext integrity check failed: computed SHA-256 '{computed_sha}' "
                f"does not match provided checksum '{sha256_hash}'."
            ),
        )

    # Plaintext heuristic: AES-256-GCM ciphertext + 16-byte tag is cryptographically pseudorandom.
    # Reject uploads that are purely printable ASCII text (e.g. unencrypted plain files).
    tag = ciphertext_bytes[-16:]
    if all(32 <= b <= 126 for b in tag):
        try:
            decoded = ciphertext_bytes.decode("utf-8")
            if decoded.isprintable():
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Unencrypted plaintext rejected: payload lacks cryptographic ciphertext characteristics.",
                )
        except UnicodeDecodeError:
            pass


@router.post("/upload", response_model=FileUploadResponse, summary="Upload Encrypted File Payload")
async def upload_encrypted_file(
    request: Request,
    consumer_user: Dict[str, Any] = Depends(require_consumer),
) -> FileUploadResponse:
    """
    Authenticated consumer uploads an AES-256-GCM encrypted file payload.
    Supports JSON body (with base64 ciphertext) or binary octet-stream (with X-* headers).
    Coordinates provider node selection, quota reservation, transient spooling,
    and queues an asynchronous STORE_CHUNK job.
    """
    content_type = request.headers.get("content-type", "").lower()

    if "application/json" in content_type:
        try:
            body_json = await request.json()
            req = FileUploadRequest(**body_json)
            filename = req.filename
            file_size = req.file_size
            iv_hex = req.iv_hex
            sha256_hash = req.sha256_hash
            try:
                ciphertext_bytes = base64.b64decode(req.ciphertext_base64)
            except Exception as exc:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Invalid base64 encoding in ciphertext_base64: {exc}",
                )
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Malformed upload request payload: {exc}",
            )
    else:
        # Binary octet-stream upload
        filename = request.headers.get("X-Filename", "encrypted_payload.bin").strip()
        filename = Path(filename).name
        file_size_header = request.headers.get("X-Plaintext-Size") or request.headers.get("X-File-Size")
        if not file_size_header:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Missing required header 'X-Plaintext-Size'.",
            )
        try:
            file_size = int(file_size_header)
        except ValueError:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Header 'X-Plaintext-Size' must be an integer.")

        iv_hex = request.headers.get("X-IV-Hex", "").strip()
        sha256_hash = request.headers.get("X-SHA256-Checksum", "").strip()
        ciphertext_bytes = await request.body()

    # 1. Cryptographic and size parameter validations
    _validate_ciphertext_cryptography(
        file_size=file_size,
        ciphertext_bytes=ciphertext_bytes,
        iv_hex=iv_hex,
        sha256_hash=sha256_hash,
    )

    # 2. Select an eligible online provider with sufficient available quota
    enc_size = len(ciphertext_bytes)
    provider_node = find_eligible_provider_node(required_bytes=enc_size)
    if not provider_node:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="No online storage provider node with sufficient available capacity is currently available.",
        )

    # 3. Transactionally reserve storage quota on provider node
    reserved = reserve_provider_quota(provider_node["id"], enc_size)
    if not reserved:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Failed to reserve storage capacity on eligible provider node.",
        )

    # 4. Create database records
    try:
        file_rec = create_file_record(
            owner_id=consumer_user["id"],
            filename=filename,
            file_size=file_size,
            encrypted_size=enc_size,
            iv_hex=iv_hex,
            sha256_hash=sha256_hash,
        )
        chunk_id = f"chunk_{uuid.uuid4().hex}"
        create_file_chunk(
            file_id=file_rec["id"],
            chunk_id=chunk_id,
            provider_node_id=provider_node["id"],
            chunk_size=enc_size,
        )

        # 5. Buffer ephemeral ciphertext in coordinator spool
        save_spool_chunk(chunk_id, ciphertext_bytes)

        # 6. Queue STORE_CHUNK job for provider agent polling
        job = create_chunk_job(
            provider_node_id=provider_node["id"],
            chunk_id=chunk_id,
            file_id=file_rec["id"],
            job_type="STORE_CHUNK",
            timeout_seconds=60,
        )

        # 7. Update file status to UPLOADING (never falsely report STORED before provider confirms)
        update_file_status(file_rec["id"], "UPLOADING")

        logger.info(
            f"Consumer #{consumer_user['id']} uploaded '{filename}' ({enc_size} enc bytes) -> "
            f"Provider Node #{provider_node['id']} (Job #{job['id']})."
        )

        return FileUploadResponse(
            file_id=file_rec["id"],
            filename=file_rec["filename"],
            file_size=file_rec["file_size"],
            encrypted_size=file_rec["encrypted_size"],
            status="UPLOADING",
            provider_node_id=provider_node["id"],
            job_id=job["id"],
            created_at=str(file_rec["created_at"]),
        )

    except Exception as exc:
        # Roll back quota reservation if error occurs during creation
        release_provider_quota(provider_node["id"], enc_size)
        logger.error(f"Failed to complete file upload setup: {exc}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to process file upload: {str(exc)}",
        )


@router.get("", response_model=List[FileStatusResponse], summary="List Consumer Files")
def list_files(
    consumer_user: Dict[str, Any] = Depends(require_consumer),
) -> List[FileStatusResponse]:
    """
    Retrieve all non-deleted files owned by the authenticated consumer.
    Strictly isolated: consumers can never see other users' files.
    """
    files = list_consumer_files(owner_id=consumer_user["id"])
    return [
        FileStatusResponse(
            file_id=f["id"],
            filename=f["filename"],
            file_size=f["file_size"],
            encrypted_size=f["encrypted_size"],
            iv_hex=f["iv_hex"],
            sha256_hash=f["sha256_hash"],
            status=f["status"],
            is_stored=(f["status"] == "STORED"),
            created_at=str(f["created_at"]),
            updated_at=str(f["updated_at"]) if f.get("updated_at") else None,
        )
        for f in files
    ]


@router.get("/{file_id}/status", response_model=FileStatusResponse, summary="Get Live File Status")
def get_file_status(
    file_id: int,
    consumer_user: Dict[str, Any] = Depends(require_consumer),
) -> FileStatusResponse:
    """
    Retrieve live status of a file owned by the authenticated consumer.
    Checks and reaps expired jobs if needed.
    """
    expire_stale_jobs()
    file_rec = get_file_by_id(file_id, owner_id=consumer_user["id"])
    if not file_rec:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File not found or access denied.",
        )

    return FileStatusResponse(
        file_id=file_rec["id"],
        filename=file_rec["filename"],
        file_size=file_rec["file_size"],
        encrypted_size=file_rec["encrypted_size"],
        iv_hex=file_rec["iv_hex"],
        sha256_hash=file_rec["sha256_hash"],
        status=file_rec["status"],
        is_stored=(file_rec["status"] == "STORED"),
        created_at=str(file_rec["created_at"]),
        updated_at=str(file_rec["updated_at"]) if file_rec.get("updated_at") else None,
    )


@router.get("/{file_id}/download", summary="Download Encrypted File Ciphertext")
async def download_encrypted_file(
    file_id: int,
    consumer_user: Dict[str, Any] = Depends(require_consumer),
) -> Response:
    """
    Download ciphertext for a consumer-owned file.
    Coordinates retrieval from the assigned provider node via RETRIEVE_CHUNK.
    Verifies SHA-256 integrity and delivers ciphertext with IV headers.
    Server NEVER holds decryption keys or decrypts data.
    """
    file_rec = get_file_by_id(file_id, owner_id=consumer_user["id"])
    if not file_rec:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File not found or access denied.",
        )

    if file_rec["status"] == "DELETED":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File has been deleted.",
        )

    if file_rec["status"] != "STORED":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"File is not ready for download. Current status is '{file_rec['status']}'.",
        )

    chunks = get_file_chunks(file_id)
    if not chunks:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No storage chunk records found for this file.",
        )

    chunk = chunks[0]

    # Check if ciphertext is already present in coordinator ephemeral spool
    ciphertext = get_spool_chunk(chunk["chunk_id"])

    if ciphertext is None:
        # Verify provider node availability
        provider_node = get_provider_node_by_id(chunk["provider_node_id"])
        if not provider_node or compute_node_effective_status(provider_node) != "ONLINE":
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Assigned storage provider node is currently offline or unreachable.",
            )

        # Check for active existing RETRIEVE_CHUNK job or create one
        active_job = get_active_chunk_job(chunk["chunk_id"], "RETRIEVE_CHUNK")
        if not active_job:
            job = create_chunk_job(
                provider_node_id=chunk["provider_node_id"],
                chunk_id=chunk["chunk_id"],
                file_id=file_id,
                job_type="RETRIEVE_CHUNK",
                timeout_seconds=30,
            )
            job_id = job["id"]
        else:
            job_id = active_job["id"]

        logger.info(
            f"Queued RETRIEVE_CHUNK Job #{job_id} for chunk '{chunk['chunk_id']}' -> "
            f"Provider Node #{chunk['provider_node_id']}. Waiting for provider handoff..."
        )

        # Await provider agent upload to coordinator spool (up to 30 seconds in 0.2s steps)
        for _ in range(150):
            await asyncio.sleep(0.2)
            ciphertext = get_spool_chunk(chunk["chunk_id"])
            if ciphertext is not None:
                break
            j_rec = get_chunk_job_by_id(job_id)
            if j_rec and j_rec["status"] in ("FAILED", "TIMED_OUT"):
                raise HTTPException(
                    status_code=status.HTTP_504_GATEWAY_TIMEOUT,
                    detail=f"Storage provider failed chunk retrieval: {j_rec.get('error_message', 'Timeout')}",
                )

        if ciphertext is None:
            raise HTTPException(
                status_code=status.HTTP_504_GATEWAY_TIMEOUT,
                detail="Storage provider node did not deliver chunk payload within the timeout window.",
            )

    # Verify SHA-256 integrity
    computed_sha = hashlib.sha256(ciphertext).hexdigest()
    if computed_sha != file_rec["sha256_hash"]:
        remove_spool_chunk(chunk["chunk_id"])
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Ciphertext SHA-256 integrity check failed upon retrieval.",
        )

    # Immediately purge transient spool buffer so coordinator holds zero persistent ciphertext
    remove_spool_chunk(chunk["chunk_id"])

    logger.info(f"Delivered ciphertext for file #{file_id} ('{file_rec['filename']}') to consumer #{consumer_user['id']}.")

    return Response(
        content=ciphertext,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f'attachment; filename="{file_rec["filename"]}.enc"',
            "X-IV-Hex": file_rec["iv_hex"],
            "X-SHA256-Checksum": file_rec["sha256_hash"],
            "X-Plaintext-Size": str(file_rec["file_size"]),
            "X-Encrypted-Size": str(file_rec["encrypted_size"]),
        },
    )


@router.delete("/{file_id}", response_model=FileDeleteResponse, summary="Delete Stored File")
async def delete_file(
    file_id: int,
    consumer_user: Dict[str, Any] = Depends(require_consumer),
) -> FileDeleteResponse:
    """
    Safely delete a consumer-owned file.
    Coordinates physical chunk deletion on provider node via DELETE_CHUNK job,
    ensures committed quota is accurately released, and marks file as DELETED.
    Never reports success before physical deletion is confirmed.
    """
    file_rec = get_file_by_id(file_id)
    if not file_rec:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File not found.",
        )

    if file_rec["owner_id"] != consumer_user["id"]:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File not found or access denied.",
        )

    if file_rec["status"] == "DELETED":
        return FileDeleteResponse(
            file_id=file_id,
            status="DELETED",
            message="File was already deleted.",
        )

    chunks = get_file_chunks(file_id)

    if file_rec["status"] == "STORED":
        if not chunks:
            update_file_status(file_id, "DELETED")
            return FileDeleteResponse(
                file_id=file_id,
                status="DELETED",
                message="File metadata marked as deleted (no remaining physical chunks).",
            )

        # Pre-validate that all assigned provider nodes are ONLINE before queuing any delete jobs
        for chunk in chunks:
            provider_node = get_provider_node_by_id(chunk["provider_node_id"])
            if not provider_node or compute_node_effective_status(provider_node) != "ONLINE":
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Assigned storage provider node is currently offline. Cannot confirm physical deletion.",
                )

        # Coordinate physical chunk deletion across provider nodes
        for chunk in chunks:
            job = get_active_chunk_job(chunk["chunk_id"], "DELETE_CHUNK")
            if not job:
                job = create_chunk_job(
                    provider_node_id=chunk["provider_node_id"],
                    chunk_id=chunk["chunk_id"],
                    file_id=file_id,
                    job_type="DELETE_CHUNK",
                    timeout_seconds=20,
                )

            # Await provider confirmation (up to 15 seconds)
            completed = False
            for _ in range(75):
                await asyncio.sleep(0.2)
                j_rec = get_chunk_job_by_id(job["id"])
                if j_rec and j_rec["status"] == "COMPLETED":
                    completed = True
                    break
                if j_rec and j_rec["status"] in ("FAILED", "TIMED_OUT"):
                    raise HTTPException(
                        status_code=status.HTTP_504_GATEWAY_TIMEOUT,
                        detail=f"Provider failed to execute chunk deletion: {j_rec.get('error_message', 'Timeout')}",
                    )

            if not completed:
                raise HTTPException(
                    status_code=status.HTTP_504_GATEWAY_TIMEOUT,
                    detail="Storage provider node did not confirm chunk deletion in time.",
                )

        update_file_status(file_id, "DELETED")
        logger.info(f"File #{file_id} ('{file_rec['filename']}') confirmed physically deleted by provider.")
        return FileDeleteResponse(
            file_id=file_id,
            status="DELETED",
            message="File and all physical storage chunks successfully deleted.",
        )

    else:
        # File was in PENDING_UPLOAD, UPLOADING, or FAILED:
        # Purge any transient spool files, release quota, and cancel pending jobs
        for chunk in chunks:
            remove_spool_chunk(chunk["chunk_id"])
            release_provider_quota(chunk["provider_node_id"], chunk["chunk_size"])

        cancel_pending_chunk_jobs_for_file(file_id)
        delete_file_chunks_for_file(file_id)
        update_file_status(file_id, "DELETED")
        return FileDeleteResponse(
            file_id=file_id,
            status="DELETED",
            message="Unstored file metadata purged and quota reservations released.",
        )
