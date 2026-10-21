from contextlib import asynccontextmanager
from typing import Any, Dict, List
import logging
import os

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

# Load environment variables (.env) from project root if present
load_dotenv()

from backend.admin_router import router as admin_router
from backend.auth_router import router as auth_router
from backend.consumer_router import router as consumer_router
from backend.provider_router import router as provider_router
from backend.database import expire_stale_jobs, get_database_info, get_db_path, get_table_names, init_db
from backend.spool import cleanup_all_spool

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("store-share-backend")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application lifecycle manager: Initializes database and cleans transient spools on startup/shutdown.
    Supports both PostgreSQL and SQLite based on configuration.
    """
    db_info = get_database_info()
    logger.info(f"Initializing Store-Share database ({db_info['engine']})...")
    try:
        init_db()
        logger.info(f"Database initialized successfully: {db_info['target']}")
        # Clean up any stale unacknowledged jobs and orphaned transient buffers from prior runs
        expired = expire_stale_jobs()
        if expired > 0:
            logger.info(f"Expired {expired} stale in-flight jobs on startup.")
        cleanup_all_spool()
    except Exception as exc:
        logger.error(f"Failed to initialize database: {exc}", exc_info=True)
        raise RuntimeError(f"Database initialization failure: {exc}") from exc
    yield
    logger.info("Store-Share backend shutting down. Purging transient buffers...")
    cleanup_all_spool()


app = FastAPI(
    title="Store-Share API",
    description="Backend API and Authentication Foundation for Store-Share decentralized storage.",
    version="0.2.0",
    lifespan=lifespan,
)

# Enable CORS for frontend development
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount routers
app.include_router(auth_router)
app.include_router(provider_router)
app.include_router(consumer_router)
app.include_router(admin_router)


class HealthResponse(BaseModel):
    status: str = Field(..., json_schema_extra={"example": "healthy"})
    service: str = Field(..., json_schema_extra={"example": "store-share-backend"})
    version: str = Field(..., json_schema_extra={"example": "0.2.0"})
    database: str = Field(..., json_schema_extra={"example": "connected"})
    database_path: str = Field(...)


class TablesResponse(BaseModel):
    status: str = Field(..., json_schema_extra={"example": "success"})
    tables: List[str] = Field(...)
    count: int = Field(...)


@app.get("/health", response_model=HealthResponse, tags=["Health"])
@app.get("/api/health", response_model=HealthResponse, include_in_schema=False)
def get_health() -> HealthResponse:
    """
    Return backend system health and database connectivity status.
    """
    try:
        db_info = get_database_info()
        tables: List[str] = get_table_names()
        db_status = "connected" if "app_metadata" in tables else "initialized_without_metadata"
        return HealthResponse(
            status="healthy",
            service="store-share-backend",
            version="0.2.0",
            database=db_status,
            database_path=db_info["target"],
        )
    except Exception as exc:
        logger.error(f"Health check failed due to database error: {exc}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Health check failed: {str(exc)}",
        )



@app.get("/db/tables", response_model=TablesResponse, tags=["Database"])
@app.get("/api/db/tables", response_model=TablesResponse, include_in_schema=False)
def list_db_tables() -> TablesResponse:
    """
    Read-only endpoint that lists all table names in the SQLite database.
    Does not accept or execute arbitrary SQL.
    """
    try:
        tables: List[str] = get_table_names()
        return TablesResponse(
            status="success",
            tables=tables,
            count=len(tables),
        )
    except Exception as exc:
        logger.error(f"Failed to query database tables: {exc}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve tables: {str(exc)}",
        )
