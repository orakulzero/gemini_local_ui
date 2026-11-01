"""
backend/app/api/conversations.py
FastAPI router for conversations, DAG branching, remote sync, and exports.
"""

from __future__ import annotations

import logging
import re
import sqlite3
import time
from typing import Any

import aiosqlite
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, Field

from backend.app.bridge.client import GeminiWebClient
from backend.app.storage.database import Database
from backend.app.storage.exporter import ThreadExporter
from backend.app.storage.repository import ConversationRepository

logger = logging.getLogger("gemini_api.conversations")
router = APIRouter(prefix="/api/conversations", tags=["conversations"])


def sanitize_filename(title: str, max_len: int = 50) -> str:
    cleaned = re.sub(r'[\/:*?"<>|]', "_", title).strip()
    return cleaned[:max_len] or "conversation"


def get_repository(request: Request) -> ConversationRepository:
    """Dependency injection resolving active ConversationRepository."""
    repo = getattr(request.app.state, "repository", None)
    if not repo:
        import backend.app.main as main_mod
        repo = getattr(main_mod, "repository", None)
    if not repo:
        db = getattr(request.app.state, "db", None)
        if not db:
            import backend.app.main as main_mod
            db = getattr(main_mod, "db", None)
        repo = ConversationRepository(db) if db else ConversationRepository(Database())
    return repo


def get_gemini_client(request: Request) -> GeminiWebClient:
    """Dependency injection resolving active GeminiWebClient."""
    from backend.app.openai.routes import get_gemini_client as resolve_client
    return resolve_client(request)


def get_optional_client(request: Request) -> GeminiWebClient | None:
    """Dependency injection returning client if configured, otherwise None."""
    try:
        from backend.app.openai.routes import get_gemini_client as resolve_client
        return resolve_client(request)
    except Exception:
        return None


# Request & Response Schemas
class CreateConversationRequest(BaseModel):
    title: str | None = "New Conversation"
    model: str | None = "gemini-2.5-pro"
    gem_id: str | None = None
    system_prompt: str | None = None


class UpdateConversationRequest(BaseModel):
    title: str | None = None
    model: str | None = None
    gem_id: str | None = None
    system_prompt: str | None = None
    is_pinned: bool | None = None
    is_archived: bool | None = None


class SwitchBranchRequest(BaseModel):
    target_message_id: int | str = Field(..., description="Message ID or sibling to switch active branch to")


class EditMessageRequest(BaseModel):
    content: str = Field(..., description="New prompt content for the message edit")
    model: str | None = None


class ForkBranchRequest(BaseModel):
    parent_id: int | str | None = None
    role: str = "user"
    content: str
    thoughts: str | None = None
    model: str | None = None


class SyncRemoteRequest(BaseModel):
    limit: int = Field(50, ge=1, le=100)
    hydrate_all: bool = False


class ConversationSummary(BaseModel):
    id: str
    title: str
    model: str
    remote_conversation_id: str | None = None
    gem_id: str | None = None
    system_prompt: str | None = None
    active_leaf_message_id: int | None = None
    is_pinned: bool
    is_archived: bool
    created_at: int
    updated_at: int
    message_count: int


class ConversationListResponse(BaseModel):
    conversations: list[ConversationSummary]
    total: int
    page: int
    limit: int
    has_more: bool


@router.get("", response_model=ConversationListResponse)
async def list_conversations(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    archived: bool = Query(False),
    pinned_first: bool = Query(True),
    search: str | None = Query(None),
    repo: ConversationRepository = Depends(get_repository),
) -> ConversationListResponse:
    """Lists conversations with pagination and pinned-first sorting."""
    convs, total = await repo.list_conversations(
        page=page,
        limit=limit,
        archived=archived,
        pinned_first=pinned_first,
        search_query=search,
    )
    has_more = (page * limit) < total
    return ConversationListResponse(
        conversations=[ConversationSummary(**c.to_dict()) for c in convs],
        total=total,
        page=page,
        limit=limit,
        has_more=has_more,
    )


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_conversation(
    req: CreateConversationRequest,
    repo: ConversationRepository = Depends(get_repository),
) -> dict[str, Any]:
    """Creates a new conversation container."""
    conv = await repo.create_conversation(
        title=req.title or "New Conversation",
        model=req.model or "gemini-2.5-pro",
        gem_id=req.gem_id,
        system_prompt=req.system_prompt,
    )
    return conv.to_dict()


@router.get("/{id}")
async def get_conversation(
    id: str,
    tree: bool = Query(True, description="Include DAG tree representation"),
    leaf_id: int | str | None = Query(None, description="Reconstruct thread to specific leaf"),
    repo: ConversationRepository = Depends(get_repository),
    client: GeminiWebClient | None = Depends(get_optional_client),
) -> dict[str, Any]:
    """Retrieves conversation metadata and reconstructed active thread or full DAG."""
    conv = await repo.get_conversation(id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    active_thread = await repo.get_active_thread(id, leaf_message_id=leaf_id)
    needs_hydration = (
        not active_thread
        or active_thread[-1].role == "user"
        or any("immersive_entry_chip" in (m.content or "") for m in active_thread)
    )
    if needs_hydration and conv.remote_conversation_id and client:
        try:
            turns = await client.get_conversation_turns(conv.remote_conversation_id)
            if turns:
                await repo.hydrate_remote_turns(id, turns)
                conv = await repo.get_conversation(id) or conv
                active_thread = await repo.get_active_thread(id, leaf_message_id=leaf_id)
        except Exception as e:
            logger.warning("Lazy remote turn hydration failed for %s: %s", conv.remote_conversation_id, e)

    resp: dict[str, Any] = {
        "conversation": conv.to_dict(),
        "active_leaf_message_id": conv.active_leaf_message_id,
        "active_thread": [m.to_dict() for m in active_thread],
    }

    if tree:
        all_msgs = await repo.get_all_messages(id)
        resp["all_messages"] = [m.to_dict() for m in all_msgs]

    return resp


@router.patch("/{id}")
async def update_conversation(
    id: str,
    req: UpdateConversationRequest,
    repo: ConversationRepository = Depends(get_repository),
) -> dict[str, Any]:
    """Updates conversation attributes."""
    conv = await repo.get_conversation(id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    update_fields = {k: v for k, v in req.model_dump().items() if v is not None}
    if "updated_at" not in update_fields:
        update_fields["updated_at"] = int(time.time() * 1000)
    updated_conv = await repo.update_conversation(id, **update_fields)
    assert updated_conv is not None
    return updated_conv.to_dict()


@router.delete("/{id}")
async def delete_conversation(
    id: str,
    remote: bool = Query(False, description="Also delete from remote Google account if linked"),
    repo: ConversationRepository = Depends(get_repository),
    client: GeminiWebClient | None = Depends(get_optional_client),
) -> dict[str, Any]:
    """Deletes conversation locally, and optionally from remote Google Gemini account."""
    conv = await repo.get_conversation(id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    remote_deleted = False
    if remote and conv.remote_conversation_id and client:
        try:
            remote_deleted = await client.delete_conversation(conv.remote_conversation_id)
        except Exception as e:
            logger.warning("Remote deletion failed for cid %s: %s", conv.remote_conversation_id, e)

    deleted = await repo.delete_conversation(id)
    return {"success": deleted, "id": id, "remote_deleted": remote_deleted}


@router.post("/sync")
async def sync_remote_conversations(
    req: SyncRemoteRequest = SyncRemoteRequest(),
    repo: ConversationRepository = Depends(get_repository),
    client: GeminiWebClient = Depends(get_gemini_client),
) -> dict[str, Any]:
    """Triggers synchronization between local database and Google Gemini web account."""
    try:
        remote_list = await client.list_remote_conversations(limit=req.limit)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed querying Google Gemini RPC: {e}") from e

    result = await repo.reconcile_remote_conversations(remote_list)

    if req.hydrate_all:
        hydrated_count = 0
        for item in remote_list:
            cid = item.get("cid")
            if not cid:
                continue
            conv = await repo.get_conversation_by_remote_id(cid)
            if conv:
                try:
                    turns = await client.get_conversation_turns(cid)
                    await repo.hydrate_remote_turns(conv.id, turns)
                    hydrated_count += 1
                except Exception as e:
                    logger.warning("Failed hydrating turns for cid %s: %s", cid, e)
        result["hydrated"] = hydrated_count

    return {"success": True, **result}


@router.get("/{id}/export")
async def export_conversation(
    id: str,
    format: str = Query("markdown", pattern="^(markdown|json)$"),
    leaf_id: int | str | None = Query(None),
    repo: ConversationRepository = Depends(get_repository),
) -> Response:
    """Exports conversation thread to clean GitHub Markdown or structured JSON."""
    conv = await repo.get_conversation(id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    thread = await repo.get_active_thread(id, leaf_message_id=leaf_id)
    safe_title = sanitize_filename(conv.title)

    if format == "markdown":
        content = ThreadExporter.export_to_markdown(conv, thread)
        filename = f"{safe_title}.md"
        media_type = "text/markdown; charset=utf-8"
    else:
        all_msgs = await repo.get_all_messages(id)
        content = ThreadExporter.export_to_json(conv, thread, all_messages=all_msgs)
        filename = f"{safe_title}.json"
        media_type = "application/json; charset=utf-8"

    return Response(
        content=content.encode("utf-8"),
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/{id}/branch")
async def switch_branch(
    id: str,
    req: SwitchBranchRequest,
    repo: ConversationRepository = Depends(get_repository),
) -> dict[str, Any]:
    """Switches active branch by resolving to leaf under target message."""
    resolved_leaf, thread = await repo.switch_active_branch(id, req.target_message_id)
    return {
        "success": True,
        "active_leaf_message_id": resolved_leaf,
        "active_thread": [m.to_dict() for m in thread],
    }


@router.post("/{id}/messages/{message_id}/edit")
async def edit_message_and_branch(
    id: str,
    message_id: int | str,
    req: EditMessageRequest,
    repo: ConversationRepository = Depends(get_repository),
) -> dict[str, Any]:
    """Edits an earlier message, creating a new sibling branch node."""
    new_msg, new_thread = await repo.create_branch_edit(
        conversation_id=id,
        target_message_id=message_id,
        new_content=req.content,
        model=req.model,
    )
    return {
        "success": True,
        "created_message": new_msg.to_dict(),
        "active_thread": [m.to_dict() for m in new_thread],
    }


@router.post("/{id}/fork")
async def fork_conversation_branch(
    id: str,
    req: ForkBranchRequest,
    repo: ConversationRepository = Depends(get_repository),
) -> dict[str, Any]:
    """Forks a new branch directly from parent_id."""
    conv = await repo.get_conversation(id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    if req.parent_id is not None:
        try:
            parent_msg = await repo.get_message(req.parent_id)
        except Exception:
            parent_msg = None

        if not parent_msg or parent_msg.conversation_id != id:
            raise HTTPException(
                status_code=400,
                detail="Invalid parent_id or parent does not belong to conversation",
            )

    try:
        new_msg, new_thread = await repo.fork_branch(
            conversation_id=id,
            parent_id=req.parent_id,
            role=req.role,
            content=req.content,
            thoughts=req.thoughts,
            model=req.model,
        )
    except (sqlite3.IntegrityError, aiosqlite.IntegrityError, ValueError) as exc:
        logger.warning("Integrity error on fork for conversation %s: %s", id, exc)
        raise HTTPException(
            status_code=400,
            detail="Invalid parent_id or parent does not belong to conversation",
        ) from exc

    return {
        "success": True,
        "created_message": new_msg.to_dict(),
        "active_thread": [m.to_dict() for m in new_thread],
    }
