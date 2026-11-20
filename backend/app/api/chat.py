"""
backend/app/api/chat.py
Thin HTTP/SSE adapter for Web UI chat endpoints (M6a Strangler-Fig).
Delegates prompt assembly, media post-processing, HITL tool runtime, and turn
orchestration to `backend.app.services`.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
import contextlib
import logging
import time
from typing import Any, Literal

from fastapi import APIRouter, Depends, File, HTTPException, Request, Response, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from backend.app.bridge.client import GeminiWebClient
from backend.app.bridge.exceptions import AuthenticationError, GeminiBridgeError
from backend.app.services import chat_orchestrator, media_postprocessor, prompt_assembler, tool_runtime
from backend.app.services.chat_orchestrator import estimate_token_count, prepare_chat_turn, stream_chat_turn
from backend.app.services.media_postprocessor import (
    _filter_unlocalized_images_meta,
    _localize_generated_images,
    _needs_image_localization,
)
from backend.app.services.stream_events import format_ui_sse, stream_with_disconnect_check
from backend.app.storage.repository import ConversationRepository

logger = logging.getLogger("gemini_api.chat")
router = APIRouter(prefix="/api/chat", tags=["Web UI Chat"])


class ChatAttachmentInput(BaseModel):
    uri: str | None = None
    filename: str
    mime_type: str = "application/octet-stream"
    file_size: int = 0
    storage_path: str | None = None


class ChatGenerateRequest(BaseModel):
    conversation_id: str | None = None
    parent_message_id: int | str | None = None
    existing_user_message_id: int | None = None
    prompt: str = ""
    model: str = "gemini-2.5-flash"
    gem_id: str | None = None
    attachments: list[ChatAttachmentInput] | None = None
    account_id: str | None = None
    feature_mode: str | None = "chat"
    incognito: bool = False
    system_instruction: str | None = None
    cid: str | None = None
    rid: str | None = None
    rcid: str | None = None
    use_global_prompt: bool | None = None
    skill_ids: list[str] | None = None
    connector_ids: list[str] | None = None
    use_rag: bool = False
    rag_top_k: int = 5


class ChatTTSRequest(BaseModel):
    text: str
    language: str | None = None
    account_id: str | None = None


class ToolApprovalDecisionRequest(BaseModel):
    decision: str


class ElicitationDecisionRequest(BaseModel):
    action: Literal["accept", "decline", "cancel"]
    content: dict[str, Any] | None = None


def get_gemini_client(request: Request) -> GeminiWebClient:
    """Dependency injection resolving active GeminiWebClient."""
    from backend.app.openai.routes import get_gemini_client as resolve_client
    return resolve_client(request)


def get_repository(request: Request) -> ConversationRepository | None:
    """Dependency injection resolving active ConversationRepository."""
    repo = getattr(request.app.state, "repository", None)
    if not repo:
        import backend.app.main as main_mod
        repo = getattr(main_mod, "repository", None)
    if not repo:
        db = getattr(request.app.state, "db", None) or getattr(__import__("backend.app.main", fromlist=["db"]), "db", None)
        if db:
            repo = ConversationRepository(db)
    return repo


def _resolve_session_manager(request: Request) -> Any:
    sm = getattr(request.app.state, "session_manager", None)
    if not sm:
        with contextlib.suppress(Exception):
            import backend.app.main as main_mod
            sm = getattr(main_mod, "session_manager", None)
    return sm


@router.post("/upload")
async def upload_chat_attachment(request: Request, file: UploadFile = File(...)) -> dict[str, Any]:
    """Upload file or archive to Google Gemini content-push and return resource URI."""
    sm = _resolve_session_manager(request)
    target_acc = request.headers.get("x-gemini-account") or (sm.active_account_id if sm else "default")
    from backend.app.bridge.uploader import GeminiUploader
    uploader = GeminiUploader(session_context=sm.get_session_context(target_acc) if sm else None, base_url=sm.base_url if sm else None)
    content = await file.read()
    filename = file.filename or "file"
    mime = file.content_type if (file.content_type and file.content_type != "application/octet-stream") else GeminiUploader.detect_mime_type(filename, data=content)
    try:
        uri = await uploader.upload_file(data=content, filename=filename, mime_type=mime)
        return {"uri": uri, "filename": filename, "mime_type": mime, "file_size": len(content)}
    except Exception as exc:
        logger.error("Failed uploading attachment %s: %s", filename, exc)
        raise HTTPException(status_code=500, detail=f"Upload failed: {exc}") from exc


@router.post("/tts")
async def chat_tts(request: Request, tts_req: ChatTTSRequest) -> Response:
    """Synthesize text-to-speech audio via Google Gemini RPC XqA3Ic."""
    sm = _resolve_session_manager(request)
    acc = tts_req.account_id or request.headers.get("x-gemini-account") or (sm.active_account_id if sm else "default")
    cli = GeminiWebClient(session_context=sm.get_session_context(acc) if sm else None, base_url=sm.base_url if sm else None, vault=sm.vault if sm else None, account_id=acc)
    try:
        return Response(content=await cli.synthesize_speech(tts_req.text, language=tts_req.language), media_type="audio/ogg")
    except AuthenticationError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except GeminiBridgeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    finally:
        with contextlib.suppress(Exception):
            await cli.close()


@router.post("/tool-approvals/{approval_id}")
async def submit_tool_approval(approval_id: str, dec_req: ToolApprovalDecisionRequest) -> dict[str, Any]:
    from backend.app.mcp.approval_gate import get_approval_gate
    if not get_approval_gate().submit_decision(approval_id, dec_req.decision):  # type: ignore[arg-type]
        raise HTTPException(status_code=404, detail="Approval request not found or expired")
    return {"ok": True, "approval_id": approval_id, "decision": dec_req.decision}


@router.post("/elicitations/{elicitation_id}")
async def submit_elicitation_decision(elicitation_id: str, el_req: ElicitationDecisionRequest) -> dict[str, Any]:
    from backend.app.mcp.elicitation_gate import get_elicitation_gate
    if not get_elicitation_gate().submit_response(elicitation_id, el_req.action, el_req.content):
        raise HTTPException(status_code=404, detail="Elicitation request not found or expired")
    return {"ok": True, "elicitation_id": elicitation_id, "action": el_req.action}


@router.get("/protocol-contract")
async def get_chat_protocol_contract() -> dict[str, Any]:
    from backend.app.contracts.agent_stream_v1 import get_protocol_contract_descriptor
    return get_protocol_contract_descriptor()


@router.post("/generate")
async def generate_chat_turn(req: ChatGenerateRequest, request: Request, repo: ConversationRepository | None = Depends(get_repository)) -> StreamingResponse:
    """Thin SSE adapter delegating chat turn preparation & streaming to chat_orchestrator."""
    override_fn = request.app.dependency_overrides.get(get_gemini_client)
    client = override_fn(request) if override_fn else get_gemini_client(request)
    prepared = await prepare_chat_turn(req, request, client=client, repo=repo)
    cancel_event = asyncio.Event()

    async def sse_event_stream() -> AsyncIterator[str]:
        gen = stream_chat_turn(prepared, cancel_event=cancel_event)
        anext_task: asyncio.Task[Any] | None = None
        last_keepalive = time.monotonic()
        try:
            while True:
                if await request.is_disconnected():
                    cancel_event.set()
                    break
                if anext_task is None:
                    anext_task = asyncio.create_task(gen.__anext__())
                done, _ = await asyncio.wait({anext_task}, timeout=0.25)
                if done:
                    try:
                        ev = anext_task.result()
                    except StopAsyncIteration:
                        anext_task = None
                        break
                    anext_task = None
                    last_keepalive = time.monotonic()
                    yield ev.to_sse_frame()
                elif time.monotonic() - last_keepalive >= 15.0:
                    last_keepalive = time.monotonic()
                    yield ": keepalive\n\n"
        finally:
            cancel_event.set()
            if anext_task is not None and not anext_task.done():
                anext_task.cancel()
                with contextlib.suppress(asyncio.CancelledError, StopAsyncIteration, Exception):
                    await anext_task
            with contextlib.suppress(Exception):
                await gen.aclose()

    headers = {"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no", "X-Trace-Id": prepared.trace_id}
    return StreamingResponse(sse_event_stream(), media_type="text/event-stream", headers=headers)


__all__ = [
    "ChatAttachmentInput", "ChatGenerateRequest", "ChatTTSRequest", "ToolApprovalDecisionRequest",
    "_filter_unlocalized_images_meta", "_localize_generated_images", "_needs_image_localization",
    "chat_orchestrator", "estimate_token_count", "format_ui_sse", "get_gemini_client", "get_repository",
    "media_postprocessor", "prompt_assembler", "router", "stream_with_disconnect_check", "tool_runtime",
]
