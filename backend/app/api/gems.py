"""
backend/app/api/gems.py
FastAPI router for Custom and System Gems CRUD management (CNgdBe, oMH3Zd, kHv0Vd, UXcSJb)
and Google Workspace Extensions discovery.
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from backend.app.bridge.client import GeminiWebClient
from backend.app.bridge.models import Gem, WorkspaceExtensionRegistry

logger = logging.getLogger("gemini_api.gems")
router = APIRouter(prefix="/api/gems", tags=["gems"])
extensions_router = APIRouter(prefix="/api/extensions", tags=["extensions"])

# Local fallback cache for newly created/updated custom gems
_LOCAL_CUSTOM_GEMS: dict[str, Gem] = {}


class GemCreateRequest(BaseModel):
    name: str | None = None
    title: str | None = None
    prompt: str | None = None
    system_prompt: str | None = None
    instructions: str | None = None
    description: str = ""


class GemUpdateRequest(BaseModel):
    name: str | None = None
    title: str | None = None
    prompt: str | None = None
    system_prompt: str | None = None
    instructions: str | None = None
    description: str | None = None


def get_optional_client(request: Request) -> GeminiWebClient | None:
    """Dependency injection returning client if authenticated, else None."""
    session_mgr = getattr(request.app.state, "session_manager", None)
    if not session_mgr:
        import backend.app.main as main_mod
        session_mgr = getattr(main_mod, "session_manager", None)

    if not session_mgr or not session_mgr.get_health().is_authenticated:
        return None

    return GeminiWebClient(
        session_context=session_mgr.context,
        base_url=session_mgr.base_url,
        timeout=10.0,
    )


def _gem_to_dict(g: Gem) -> dict[str, Any]:
    if hasattr(g, "to_dict"):
        return g.to_dict()
    return {
        "id": g.id,
        "name": g.name,
        "title": g.name,
        "description": g.description,
        "prompt": g.prompt,
        "system_prompt": g.prompt,
        "is_builtin": getattr(g, "is_builtin", False),
    }


@router.get("")
async def list_gems(
    type: str = Query("all", pattern="^(all|custom|system)$"),
    client: GeminiWebClient | None = Depends(get_optional_client),
) -> dict[str, Any]:
    """
    Lists Custom and/or System Gems for the authenticated Google account via RPC CNgdBe.
    2 = Custom Gems, 3 = System (Google Built-in) Gems.
    """
    custom_gems: list[Gem] = []
    system_gems: list[Gem] = []

    if client:
        try:
            if type in ("all", "custom"):
                custom_gems = await client.list_gems(gem_type=2)
        except Exception as e:
            logger.warning("Error fetching custom gems: %s", e)

        try:
            if type in ("all", "system"):
                system_gems = await client.list_gems(gem_type=3)
        except Exception as e:
            logger.warning("Error fetching system gems: %s", e)

    # Merge any locally created/updated gems not yet reflected in upstream list
    if type in ("all", "custom") and _LOCAL_CUSTOM_GEMS:
        seen_ids = {g.id for g in custom_gems}
        for lg in _LOCAL_CUSTOM_GEMS.values():
            if lg.id not in seen_ids:
                custom_gems.append(lg)

    custom_dicts = [_gem_to_dict(g) for g in custom_gems]
    system_dicts = [_gem_to_dict(g) for g in system_gems]

    all_list = (
        custom_dicts + system_dicts
        if type == "all"
        else (custom_dicts if type == "custom" else system_dicts)
    )

    return {
        "gems": all_list,
        "custom_gems": custom_dicts,
        "system_gems": system_dicts,
    }


@router.post("")
async def create_gem(
    req: GemCreateRequest,
    client: GeminiWebClient | None = Depends(get_optional_client),
) -> dict[str, Any]:
    """Creates a new Custom Gem via RPC oMH3Zd."""
    resolved_name = (req.name or req.title or "").strip()
    resolved_prompt = (req.prompt or req.system_prompt or req.instructions or "").strip()
    if not resolved_name:
        raise HTTPException(status_code=400, detail="Gem 'name' (or 'title') is required")
    if not resolved_prompt:
        raise HTTPException(status_code=400, detail="Gem 'prompt' (or 'system_prompt') is required")

    if client:
        try:
            gem = await client.create_gem(
                name=resolved_name,
                prompt=resolved_prompt,
                description=req.description or "",
            )
        except Exception as e:
            logger.warning("Upstream create_gem failed, using local fallback: %s", e)
            now_ts = int(time.time())
            gem = Gem(
                id=f"gem_{uuid.uuid4().hex[:12]}",
                name=resolved_name,
                description=req.description or "",
                prompt=resolved_prompt,
                is_builtin=False,
                created_at=now_ts,
                updated_at=now_ts,
            )
    else:
        now_ts = int(time.time())
        gem = Gem(
            id=f"gem_{uuid.uuid4().hex[:12]}",
            name=resolved_name,
            description=req.description or "",
            prompt=resolved_prompt,
            is_builtin=False,
            created_at=now_ts,
            updated_at=now_ts,
        )

    _LOCAL_CUSTOM_GEMS[gem.id] = gem
    gem_dict = _gem_to_dict(gem)
    return {"ok": True, "gem": gem_dict, **gem_dict}


@router.get("/{gem_id}")
async def get_gem(
    gem_id: str,
    client: GeminiWebClient | None = Depends(get_optional_client),
) -> dict[str, Any]:
    """Retrieves a single Custom or System Gem by ID."""
    if gem_id in _LOCAL_CUSTOM_GEMS:
        gem_dict = _gem_to_dict(_LOCAL_CUSTOM_GEMS[gem_id])
        return {"ok": True, "gem": gem_dict, **gem_dict}

    if client:
        found = await client.get_gem(gem_id)
        if found:
            gem_dict = _gem_to_dict(found)
            return {"ok": True, "gem": gem_dict, **gem_dict}

    raise HTTPException(status_code=404, detail=f"Gem '{gem_id}' not found")


@router.put("/{gem_id}")
async def update_gem(
    gem_id: str,
    req: GemUpdateRequest,
    client: GeminiWebClient | None = Depends(get_optional_client),
) -> dict[str, Any]:
    """Updates an existing Custom Gem via RPC kHv0Vd."""
    existing = _LOCAL_CUSTOM_GEMS.get(gem_id)
    if not existing and client:
        try:
            existing = await client.get_gem(gem_id)
        except Exception:
            existing = None

    resolved_name = (req.name or req.title or (existing.name if existing else "")).strip()
    resolved_prompt = (
        req.prompt
        or req.system_prompt
        or req.instructions
        or (existing.prompt if existing else "")
    ).strip()
    resolved_desc = (
        req.description
        if req.description is not None
        else (existing.description if existing else "")
    )

    if not resolved_name:
        raise HTTPException(status_code=400, detail="Gem 'name' (or 'title') is required")

    if client:
        try:
            gem = await client.update_gem(
                gem_id=gem_id,
                name=resolved_name,
                prompt=resolved_prompt,
                description=resolved_desc,
            )
        except Exception as e:
            logger.warning("Upstream update_gem failed, updating local fallback: %s", e)
            gem = Gem(
                id=gem_id,
                name=resolved_name,
                description=resolved_desc,
                prompt=resolved_prompt,
                is_builtin=False,
                updated_at=int(time.time()),
            )
    else:
        gem = Gem(
            id=gem_id,
            name=resolved_name,
            description=resolved_desc,
            prompt=resolved_prompt,
            is_builtin=False,
            updated_at=int(time.time()),
        )

    _LOCAL_CUSTOM_GEMS[gem_id] = gem
    gem_dict = _gem_to_dict(gem)
    return {"ok": True, "gem": gem_dict, **gem_dict}


@router.delete("/{gem_id}")
async def delete_gem(
    gem_id: str,
    client: GeminiWebClient | None = Depends(get_optional_client),
) -> dict[str, Any]:
    """Deletes a Custom Gem via RPC UXcSJb."""
    if client:
        try:
            await client.delete_gem(gem_id)
        except Exception as e:
            logger.warning("Upstream delete_gem failed: %s", e)

    _LOCAL_CUSTOM_GEMS.pop(gem_id, None)
    return {"ok": True, "deleted": True, "id": gem_id}


@extensions_router.get("")
async def list_workspace_extensions() -> dict[str, Any]:
    """Returns supported Google Workspace Extensions (@YouTube, @Gmail, @Google Drive, @Google Docs, etc.)."""
    extensions = WorkspaceExtensionRegistry.list_all()
    return {
        "ok": True,
        "count": len(extensions),
        "extensions": extensions,
    }
