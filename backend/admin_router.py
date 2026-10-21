"""
Admin Router for Store-Share.

Provides administrator-only governance and physical storage tracking endpoints.
Strictly enforces server-side role-based authorization (ADMIN role required).
Exposes NO plaintext consumer files, absolute filesystem paths, or encryption keys.
"""

import logging
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, status

from backend.database import get_admin_file_storage_records
from backend.dependencies import require_admin
from backend.schemas import AdminFileStorageTrackingResponse

logger = logging.getLogger("store-share-admin")
router = APIRouter(prefix="/api/admin", tags=["Admin Governance"])


@router.get(
    "/files/storage",
    response_model=List[AdminFileStorageTrackingResponse],
    summary="Trace Consumer Files to Physical Provider Nodes",
    status_code=status.HTTP_200_OK,
)
def get_file_storage_tracking(
    admin_user: Dict[str, Any] = Depends(require_admin),
) -> List[Dict[str, Any]]:
    """
    Administrator-only endpoint to trace every consumer file to its physical provider node(s).
    
    Returns comprehensive metadata:
    - File ID, filename, creation time, encrypted size, and lifecycle status
    - Consumer ID and email
    - Chunk ID and chunk index
    - Provider node ID, provider email, and provider computer name
    - Provider online/offline status and last heartbeat
    - Provider allocated quota and used storage
    - Physical presence verification status (VERIFIED_PHYSICAL, PROVIDER_OFFLINE, PHYSICALLY_MISSING, DATABASE_ASSIGNED, DELETED)
    - Latest STORE_CHUNK, RETRIEVE_CHUNK, and DELETE_CHUNK job statuses and timestamps
    - Full list of all chunks for multi-chunk file distribution

    Strictly denies access to anonymous callers (HTTP 401) and consumers/providers (HTTP 403).
    """
    logger.info(f"Admin #{admin_user['id']} ({admin_user['email']}) accessed file storage tracking.")
    return get_admin_file_storage_records()
