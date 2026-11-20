"""
backend/app/api/rag.py
REST API router (`/api/rag/*`) for Local Hybrid RAG status, indexing, search, and reset.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
import tempfile
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from backend.app.rag.hybrid_search import HybridRAGService
from backend.app.security.path_sandbox import validate_safe_path

logger = logging.getLogger("gemini_api.rag")
router = APIRouter(prefix="/api/rag", tags=["Hybrid RAG"])


class RagIndexRequest(BaseModel):
    source_types: list[str] | None = None
    workspace_dir: str | None = None


class RagSearchRequest(BaseModel):
    query: str
    top_k: int = Field(default=8, ge=1, le=50)
    mode: Literal["hybrid", "fts5", "vector"] = "hybrid"
    source_types: list[str] | None = None
    rerank: bool = True


def get_rag_service(request: Request | None = None) -> HybridRAGService:
    """Resolve active HybridRAGService singleton from FastAPI app state or main module."""
    if request is not None:
        svc = getattr(request.app.state, "rag_service", None)
        if svc is not None:
            return svc
    import backend.app.main as main_mod

    svc = getattr(main_mod.app.state, "rag_service", None)
    if svc is None:
        db = getattr(request.app.state, "db", None) if request is not None else None
        db = db or main_mod.db
        svc = HybridRAGService(db)
        main_mod.app.state.rag_service = svc
    return svc


def _validate_workspace_dir(raw_dir: str) -> Path:
    repo_root = Path(__file__).resolve().parents[3]
    allowed_roots = [repo_root, Path.home(), Path(tempfile.gettempdir())]
    extra_roots = os.environ.get("GEMINI_RAG_ALLOWED_ROOTS", "").strip()
    if extra_roots:
        for part in extra_roots.split(os.pathsep):
            if part.strip():
                allowed_roots.append(Path(part.strip()))
    try:
        resolved = validate_safe_path(raw_dir, allowed_roots=allowed_roots)
    except (ValueError, PermissionError) as exc:
        raise HTTPException(status_code=400, detail=f"Invalid workspace_dir: {exc}") from exc
    if not resolved.exists() or not resolved.is_dir():
        raise HTTPException(status_code=400, detail="workspace_dir does not exist or is not a directory")
    return resolved


@router.get("/status")
async def rag_status(request: Request) -> dict[str, Any]:
    svc = get_rag_service(request)
    return await svc.get_status()


@router.post("/index")
async def rag_index(req: RagIndexRequest, request: Request) -> dict[str, Any]:
    svc = get_rag_service(request)
    ws_path = _validate_workspace_dir(req.workspace_dir) if req.workspace_dir else None
    source_types = req.source_types
    if source_types is None:
        source_types = ["workspace_file"] if ws_path is not None else ["conversation", "brain_digest"]
    return await svc.index_sources(source_types=source_types, workspace_dir=ws_path)


@router.post("/search")
async def rag_search(req: RagSearchRequest, request: Request) -> dict[str, Any]:
    if not req.query or not req.query.strip():
        raise HTTPException(status_code=400, detail="Search query must not be empty")
    svc = get_rag_service(request)
    return await svc.search(
        req.query,
        top_k=req.top_k,
        mode=req.mode,
        source_types=req.source_types,
        rerank=req.rerank,
    )


@router.delete("/index")
async def rag_clear_index(
    request: Request,
    source_type: str | None = Query(default=None),
) -> dict[str, Any]:
    svc = get_rag_service(request)
    return await svc.clear_index(source_type=source_type)
