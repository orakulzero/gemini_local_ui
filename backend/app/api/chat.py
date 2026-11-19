"""
backend/app/api/chat.py
Local Web UI SSE streaming generation endpoint with SQLite persistence.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any
from collections.abc import AsyncIterator
import uuid
from fastapi import APIRouter, Depends, File, HTTPException, Request, Response, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from backend.app.bridge.client import GeminiWebClient
from backend.app.bridge.exceptions import (
    AuthenticationError,
    GeminiBridgeError,
    ModelInconsistentError,
)
from backend.app.bridge.models import DEFAULT_MODELS
from backend.app.mcp.tool_fence_filter import ToolFenceFilter, strip_tool_call_fences
from backend.app.storage.repository import ConversationRepository
import contextlib

logger = logging.getLogger("gemini_api.chat")
router = APIRouter(prefix="/api/chat", tags=["Web UI Chat"])


async def stream_with_disconnect_check(
    gen: AsyncIterator[Any],
    request: Request,
    timeout: float = 1.0,
    max_queue_size: int = 64,
) -> AsyncIterator[Any]:
    """
    Wraps an async generator with periodic non-blocking client disconnect checks
    and bounded queue backpressure.
    Ticks every `timeout` seconds (default 1.0s) so that prolonged reasoning
    silence (60-180s without tokens) does not block disconnect detection.
    """
    queue: asyncio.Queue[tuple[bool, Any]] = asyncio.Queue(maxsize=max_queue_size)
    stop_event = asyncio.Event()

    async def producer() -> None:
        try:
            async for item in gen:
                if stop_event.is_set():
                    break
                await queue.put((False, item))
            await queue.put((True, None))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await queue.put((True, exc))

    producer_task = asyncio.create_task(producer())
    try:
        while True:
            if await request.is_disconnected():
                logger.info("Client disconnected during streaming/reasoning; stopping generation")
                break
            try:
                is_done, val = await asyncio.wait_for(queue.get(), timeout=timeout)
                if is_done:
                    if isinstance(val, Exception):
                        raise val
                    break
                yield val
            except asyncio.TimeoutError:
                # 1.0s timeout tick during reasoning silence; loop back to check is_disconnected
                continue
    finally:
        stop_event.set()
        if not producer_task.done():
            producer_task.cancel()
        await asyncio.gather(producer_task, return_exceptions=True)
        aclose_fn = getattr(gen, "aclose", None)
        if callable(aclose_fn):
            try:
                await aclose_fn()
            except Exception as e:
                logger.debug("Suppressed error closing upstream generator: %s", e)



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


class ChatTTSRequest(BaseModel):
    text: str
    language: str | None = None
    account_id: str | None = None


def format_ui_sse(event_data: dict[str, Any]) -> str:
    """Formats event dict as a standard SSE data line for the Web UI."""
    return f"data: {json.dumps(event_data, separators=(',', ':'))}\n\n"


def estimate_token_count(text: str) -> int:
    """Estimates tokens using calibrated multi-lingual sentencepiece heuristics."""
    if not text:
        return 0
    cyrillic_chars = sum(1 for c in text if "\u0400" <= c <= "\u04FF")
    if cyrillic_chars > len(text) * 0.3:
        return max(1, int(len(text) / 1.8))
    return max(1, int(len(text) / 3.8))


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
        db = getattr(request.app.state, "db", None)
        if not db:
            import backend.app.main as main_mod
            db = getattr(main_mod, "db", None)
        if db:
            repo = ConversationRepository(db)
    return repo


def _needs_image_localization(url: Any) -> bool:
    if not isinstance(url, str) or url.startswith("/files/"):
        return False
    return (
        "googleusercontent.com/gg" in url
        or "googleusercontent.com/rd-gg" in url
        or "fife.usercontent.google.com/" in url
    )


def _filter_unlocalized_images_meta(metadata: dict[str, Any]) -> dict[str, Any]:
    safe_meta = dict(metadata)
    for key in ("images", "generated_images"):
        if key in safe_meta and isinstance(safe_meta[key], list):
            safe_list = [
                img
                for img in safe_meta[key]
                if not (isinstance(img, dict) and _needs_image_localization(img.get("url")))
            ]
            if safe_list:
                safe_meta[key] = safe_list
            else:
                safe_meta.pop(key, None)
    return safe_meta


async def _localize_generated_images(
    images_list: list[dict[str, Any]],
    active_cli: Any,
    cid: str | None,
    rid: str | None,
    rcid: str | None,
    data_dir: Any = None,
) -> bool:
    """
    Downloads remote Google gg/gg-dl images, saves locally to data/images via save_image,
    and updates images in-place to /files/<uuid32>.<ext>. Leaves non-gg/mock URLs untouched.
    """
    if not images_list:
        return False

    from backend.app.bridge.image_downloader import GeminiImageDownloader
    from backend.app.openai.image_store import save_image

    downloader = GeminiImageDownloader(client=active_cli)
    modified = False

    for img in images_list:
        if not isinstance(img, dict):
            continue
        raw_url = img.get("url")
        if not isinstance(raw_url, str):
            continue
        if not _needs_image_localization(raw_url):
            continue

        meta = {
            "url": raw_url,
            "image_id": img.get("image_id", ""),
            "placeholder": img.get("placeholder", ""),
            "cid": cid or "",
            "rid": rid or "",
            "rcid": rcid or "",
        }
        try:
            img_bytes, mime = await downloader.download_image(meta)
            if img_bytes:
                saved_name = save_image(img_bytes, mime or "image/png", data_dir=data_dir)
                img["original_url"] = raw_url
                img["url"] = f"/files/{saved_name}"
                modified = True
        except Exception as dl_err:
            logger.warning("Failed localizing image from %s: %s", raw_url[:60], dl_err)

    return modified


@router.post("/upload")
async def upload_chat_attachment(
    request: Request,
    file: UploadFile = File(...),
) -> dict[str, Any]:
    """Uploads file or archive to Google Gemini content-push and returns resource URI."""
    session_mgr = getattr(request.app.state, "session_manager", None)
    if not session_mgr:
        import backend.app.main as main_mod
        session_mgr = getattr(main_mod, "session_manager", None)

    target_account_id = request.headers.get("x-gemini-account") or (session_mgr.active_account_id if session_mgr else "default")
    ctx = session_mgr.get_session_context(target_account_id) if session_mgr else None

    from backend.app.bridge.uploader import GeminiUploader
    uploader = GeminiUploader(session_context=ctx, base_url=session_mgr.base_url if session_mgr else None)

    content = await file.read()
    filename = file.filename or "file"
    mime_type = (
        file.content_type
        if (file.content_type and file.content_type != "application/octet-stream")
        else GeminiUploader.detect_mime_type(filename, data=content)
    )

    try:
        uri = await uploader.upload_file(
            data=content,
            filename=filename,
            mime_type=mime_type,
        )
        return {
            "uri": uri,
            "filename": filename,
            "mime_type": mime_type,
            "file_size": len(content),
        }
    except Exception as e:
        logger.error("Failed uploading attachment %s: %s", filename, e)
        raise HTTPException(status_code=500, detail=f"Upload failed: {e}")


@router.post("/tts")
async def chat_tts(
    request: Request,
    tts_req: ChatTTSRequest,
) -> Response:
    """Synthesizes text-to-speech audio via Google Gemini RPC XqA3Ic."""
    session_mgr = getattr(request.app.state, "session_manager", None)
    if not session_mgr:
        try:
            import backend.app.main as main_mod
            session_mgr = getattr(main_mod, "session_manager", None)
        except Exception:
            session_mgr = None

    target_acc_id = (
        tts_req.account_id
        or request.headers.get("x-gemini-account")
        or (session_mgr.active_account_id if session_mgr else "default")
    )
    ctx = session_mgr.get_session_context(target_acc_id) if session_mgr else None
    client = GeminiWebClient(
        session_context=ctx,
        base_url=session_mgr.base_url if session_mgr else None,
        vault=session_mgr.vault if session_mgr else None,
        account_id=target_acc_id,
    )
    try:
        audio_bytes = await client.synthesize_speech(tts_req.text, language=tts_req.language)
        return Response(content=audio_bytes, media_type="audio/ogg")
    except AuthenticationError as e:
        raise HTTPException(status_code=401, detail=str(e))
    except GeminiBridgeError as e:
        raise HTTPException(status_code=502, detail=str(e))
    finally:
        try:
            await client.close()
        except Exception:
            pass


class ToolApprovalDecisionRequest(BaseModel):
    decision: str


@router.post("/tool-approvals/{approval_id}")
async def submit_tool_approval(approval_id: str, dec_req: ToolApprovalDecisionRequest):
    from backend.app.mcp.approval_gate import get_approval_gate
    gate = get_approval_gate()
    ok = gate.submit_decision(approval_id, dec_req.decision)
    if not ok:
        raise HTTPException(status_code=404, detail="Approval request not found or expired")
    return {"ok": True, "approval_id": approval_id, "decision": dec_req.decision}


@router.post("/generate")
async def generate_chat_turn(
    req: ChatGenerateRequest,
    request: Request,
    client: GeminiWebClient = Depends(get_gemini_client),
    repo: ConversationRepository | None = Depends(get_repository),
) -> StreamingResponse:
    """
    Streaming chat endpoint for the local UI with automatic quota failover.
    Persists user message and assistant completion in SQLite, supporting DAG branching.
    """
    session_mgr = getattr(request.app.state, "session_manager", None)
    if not session_mgr:
        import backend.app.main as main_mod
        session_mgr = getattr(main_mod, "session_manager", None)

    db = getattr(request.app.state, "db", None)
    if not db:
        import backend.app.main as main_mod
        db = getattr(main_mod, "db", None)

    # Resolve target account
    raw_acc = request.headers.get("x-gemini-account")
    if raw_acc in ("None", "null", ""):
        raw_acc = None
    req_acc = req.account_id if req.account_id not in ("None", "null", "") else None
    target_account_id = raw_acc or req_acc or (session_mgr.active_account_id if session_mgr else "default")
    if target_account_id in ("None", "null", ""):
        target_account_id = session_mgr.active_account_id if session_mgr else "default"
    if session_mgr:
        ctx = session_mgr.get_session_context(target_account_id)
        client = GeminiWebClient(
            session_context=ctx,
            base_url=session_mgr.base_url,
            vault=session_mgr.vault,
            account_id=target_account_id,
        )

    is_incognito = bool(req.incognito)
    from backend.app.bridge.uploader import GeminiUploader
    is_live_upload_host = "content-push.googleapis.com" in GeminiUploader(
        base_url=session_mgr.base_url if session_mgr else None
    ).upload_url

    def _to_bridge_attachment(att: Any) -> dict[str, Any]:
        mime = getattr(att, "mime_type", None) or ""
        return {
            "uri": att.uri,
            "filename": att.filename,
            "mime_type": mime,
            "scotty": is_live_upload_host and mime.startswith("image/"),
        }

    raw_user_prompt = req.prompt
    bridge_attachments = None
    if req.attachments:
        bridge_attachments = [
            _to_bridge_attachment(a)
            for a in req.attachments
            if a.uri
        ]

    if req.existing_user_message_id is not None and repo:
        existing_msg = await repo.get_message(int(req.existing_user_message_id))
        if existing_msg:
            if not raw_user_prompt.strip():
                raw_user_prompt = existing_msg.content
            if not req.attachments and existing_msg.attachments:
                bridge_attachments = [
                    _to_bridge_attachment(a)
                    for a in existing_msg.attachments
                    if a.uri
                ]

    # Workspace Prompt, Skill & MCP Trigger Decomposition
    from backend.app.api.workspace import get_mcp_manager, get_workspace_store
    from backend.app.mcp.client_manager import MCPConnectorManager

    clean_user_prompt = raw_user_prompt
    skill_injections: list[str] = []

    m_slash = re.match(r"^/([a-zA-Z0-9_-]+)(?:\s+(.*))?$", clean_user_prompt, re.DOTALL)
    if m_slash:
        slug, rem = m_slash.group(1), m_slash.group(2) or ""
        store = get_workspace_store()
        for s in store.list_skills():
            if s.id == slug or f"/{slug}" in s.triggers:
                clean_user_prompt = rem.strip()
                skill_injections.append(f"[Skill: {s.name}]\n{s.content}\n[End Skill]")
                break

    if req.skill_ids is not None:
        store = get_workspace_store()
        for sid in req.skill_ids:
            for s in store.list_skills():
                if s.id == sid:
                    skill_injections.append(f"[Skill: {s.name}]\n{s.content}\n[End Skill]")
                    break
    else:
        store = get_workspace_store()
        for s in store.list_skills():
            if s.is_active:
                skill_injections.append(f"[Skill: {s.name}]\n{s.content}\n[End Skill]")

    global_prompt_str = ""
    if req.use_global_prompt:
        active_p = get_workspace_store().get_active_prompt()
        if active_p:
            global_prompt_str = f"[Global Persona: {active_p.name}]\n{active_p.content}\n[End Global Persona]"

    prompt_parts = []
    if global_prompt_str:
        prompt_parts.append(global_prompt_str)
    if skill_injections:
        prompt_parts.extend(skill_injections)
    if req.system_instruction and not req.gem_id:
        prompt_parts.append(
            f"[System Instructions]\n"
            f"{req.system_instruction.strip()}\n"
            f"[End System Instructions]"
        )
    prompt_parts.append(clean_user_prompt)
    effective_prompt = "\n\n".join(p for p in prompt_parts if p)

    # Resolve active MCP tools
    active_tools: list[dict[str, Any]] = []
    target_conn_ids = req.connector_ids
    if target_conn_ids is None:
        enabled_conns = [c.id for c in get_workspace_store().list_connectors(mask=False) if c.enabled]
        if enabled_conns:
            target_conn_ids = enabled_conns
    if target_conn_ids:
        try:
            active_tools = await get_mcp_manager().list_active_tools(connector_ids=target_conn_ids)
            if active_tools:
                tools_prompt = MCPConnectorManager.build_connector_tools_prompt(active_tools)
                effective_prompt = f"{effective_prompt}\n\n{tools_prompt}"
        except Exception as e:
            logger.debug("Failed resolving active MCP tools: %s", e)

    if not clean_user_prompt.strip() and not req.attachments and not bridge_attachments:
        raise HTTPException(status_code=400, detail="Prompt or attachments must be provided")

    conv_id = req.conversation_id or f"conv_{uuid.uuid4().hex[:12]}"
    user_msg_id: int | None = req.existing_user_message_id
    now_ms = int(time.time() * 1000)

    saved_user_prompt = clean_user_prompt
    if req.system_instruction and not req.gem_id:
        saved_user_prompt = (
            f"[System Instructions]\n"
            f"{req.system_instruction.strip()}\n"
            f"[End System Instructions]\n\n"
            f"{clean_user_prompt}"
        )

    # 1. Persist User Message in SQLite (clean prompt without injected personas/skills)
    if not is_incognito and repo and user_msg_id is None:
        try:
            existing_conv = await repo.get_conversation(conv_id)
            if not existing_conv:
                await repo.create_conversation(
                    title=clean_user_prompt[:50] if clean_user_prompt else "New Conversation",
                    model=req.model,
                    gem_id=req.gem_id,
                    conversation_id=conv_id,
                )

            user_msg = await repo.add_message(
                conversation_id=conv_id,
                role="user",
                content=saved_user_prompt,
                parent_id=int(req.parent_message_id) if req.parent_message_id is not None and str(req.parent_message_id).isdigit() else None,
                model=req.model,
                created_at=now_ms,
            )
            user_msg_id = user_msg.id

            if req.attachments:
                for att in req.attachments:
                    await repo.add_attachment(
                        message_id=user_msg.id,
                        filename=att.filename,
                        mime_type=att.mime_type,
                        size_bytes=att.file_size,
                        storage_path=att.storage_path or "",
                        uri=att.uri or "",
                    )
        except Exception as e:
            logger.warning("Failed persisting user message in repository: %s", e)
    elif is_incognito and user_msg_id is None:
        # Use ephemeral in-memory timestamp ID without SQLite writes
        user_msg_id = int(time.time() * 1000)

    # 2. Format attachments for Gemini bridge client if not already resolved
    if req.attachments and not bridge_attachments:
        bridge_attachments = [
            _to_bridge_attachment(a)
            for a in req.attachments
            if a.uri
        ]

    # 3. Stream Generator with Automatic Quota Failover
    async def sse_event_stream() -> AsyncIterator[str]:
        client_active = None
        backup_cli = None
        # Announce start
        yield format_ui_sse({
            "type": "start",
            "conversation_id": conv_id,
            "user_message_id": user_msg_id,
        })

        start_time = time.time()
        full_text_list: list[str] = []
        full_thought_list: list[str] = []
        text_emitted = False

        current_acc_id = target_account_id
        original_account_id = target_account_id
        failover_occurred = False
        failover_from = None

        orig_sess = session_mgr.get_account_session(current_acc_id) if session_mgr else None
        fulfilled_by_id = current_acc_id
        fulfilled_by_name = orig_sess.profile.name if orig_sess and orig_sess.profile else current_acc_id

        # Determine model tier requirements
        is_task_or_research = (req.feature_mode or "").lower() in (
            "task", "tasks", "40", "spark", "deep-research", "deep_research", "deepresearch", "research"
        )
        is_high_tier_mode = (
            req.model in ("95a4c0abcba24364", "e6fa609c3fa255c0", "gemini-4-carbon")
            or "pro" in req.model.lower()
            or "argon" in req.model.lower()
            or "carbon" in req.model.lower()
            or is_task_or_research
        )
        required_tier = "pro" if is_high_tier_mode else "free"

        # Pre-flight cooldown check
        if session_mgr and session_mgr.is_in_cooldown(current_acc_id, model_id=req.model):
            backup = session_mgr.get_failover_account(req.model, current_acc_id, required_tier=required_tier)
            if backup:
                failover_occurred = True
                failover_from = current_acc_id
                fulfilled_by_id = backup.account_id
                fulfilled_by_name = backup.profile.name
                yield format_ui_sse({
                    "type": "failover",
                    "from": orig_sess.profile.name if orig_sess and orig_sess.profile else current_acc_id,
                    "to": backup.profile.name,
                    "from_account_id": current_acc_id,
                    "to_account_id": backup.account_id,
                    "error_code": 1037,
                    "reason": "Account in cooldown",
                    "model": req.model,
                    "timestamp": int(time.time()),
                })
                current_acc_id = backup.account_id
                client_active = GeminiWebClient(session_context=backup.context, base_url=session_mgr.base_url)
            else:
                client_active = client
        else:
            client_active = client

        effective_model = req.model
        if is_task_or_research and "@mode=" not in effective_model:
            mode_suffix = "deep-research" if "research" in (req.feature_mode or "").lower() else "task"
            effective_model = f"{effective_model}@mode={mode_suffix}"

        eff_cid: str | None = req.cid
        eff_rid: str | None = req.rid
        eff_rcid: str | None = req.rcid

        # Resolve prior (cid, rid, rcid) for multi-turn continuation (skipped on regeneration to avoid Error 1050)
        if not is_incognito and repo and not eff_cid and req.existing_user_message_id is None:
            try:
                candidate_msg = None
                if req.parent_message_id is not None and str(req.parent_message_id).isdigit():
                    parent_msg = await repo.get_message(int(req.parent_message_id))
                    if (
                        parent_msg
                        and parent_msg.role == "assistant"
                        and isinstance(parent_msg.metadata, dict)
                        and parent_msg.metadata.get("cid")
                    ):
                        candidate_msg = parent_msg

                if candidate_msg is None:
                    thread_msgs = await repo.get_active_thread(conv_id)
                    for msg in reversed(thread_msgs):
                        if (
                            msg.role == "assistant"
                            and isinstance(msg.metadata, dict)
                            and msg.metadata.get("cid")
                        ):
                            candidate_msg = msg
                            break

                if candidate_msg is None:
                    all_msgs = await repo.get_all_messages(conv_id)
                    for msg in reversed(all_msgs):
                        if (
                            msg.role == "assistant"
                            and isinstance(msg.metadata, dict)
                            and msg.metadata.get("cid")
                        ):
                            candidate_msg = msg
                            break

                if candidate_msg is not None:
                    meta = candidate_msg.metadata or {}
                    prev_acc = meta.get("fulfilled_by_account_id")
                    if not prev_acc or prev_acc == current_acc_id:
                        eff_cid = str(meta["cid"])
                        eff_rid = str(meta["rid"]) if meta.get("rid") else None
                        eff_rcid = str(meta["rcid"]) if meta.get("rcid") else None
            except Exception as ctx_err:
                logger.debug("Failed resolving prior (cid, rid, rcid) for %s: %s", conv_id, ctx_err)

        recorded_tool_calls: list[dict[str, Any]] = []

        async def run_stream(
            active_cli: GeminiWebClient,
            stream_cid: str | None = None,
            stream_rid: str | None = None,
            stream_rcid: str | None = None,
            prompt_override: str | None = None,
        ) -> AsyncIterator[Any]:
            async for chk in active_cli.generate_stream(
                prompt=prompt_override or effective_prompt,
                model=effective_model,
                gem_id=req.gem_id,
                cid=stream_cid,
                rid=stream_rid,
                rcid=stream_rcid,
                attachments=bridge_attachments,
                incognito=is_incognito,
                feature_mode=40 if is_task_or_research else None,
            ):
                yield chk

        accumulator_citations: list[Any] = []
        accumulator_images: list[Any] = []
        accumulator_grounding: dict[str, Any] = {}
        accumulator_youtube: list[Any] = []
        accumulator_code_executions: list[Any] = []
        done_cid: str | None = eff_cid
        done_rid: str | None = eff_rid
        done_rcid: str | None = eff_rcid

        try:
            try:
                current_stream_prompt = effective_prompt
                tool_rounds = 0
                max_tool_rounds = 2 if active_tools else 1

                while tool_rounds < max_tool_rounds:
                    tool_rounds += 1
                    has_tool_call = False
                    round_full_text: list[str] = []
                    raw_round_text: list[str] = []
                    fence_filter = ToolFenceFilter() if active_tools else None

                    async for chunk in stream_with_disconnect_check(
                        run_stream(client_active, eff_cid, eff_rid, eff_rcid, prompt_override=current_stream_prompt),
                        request,
                        timeout=1.0,
                    ):
                        if chunk.type == "thought":
                            full_thought_list.append(chunk.delta)
                            yield format_ui_sse({"type": "thought", "delta": chunk.delta})
                        elif chunk.type == "text":
                            raw_round_text.append(chunk.delta)
                            filtered_delta = fence_filter.feed(chunk.delta) if fence_filter else chunk.delta
                            if filtered_delta:
                                text_emitted = True
                                round_full_text.append(filtered_delta)
                                yield format_ui_sse({"type": "text", "delta": filtered_delta})
                        elif chunk.type in ("citation", "grounding", "media"):
                            if chunk.type == "citation" and chunk.metadata:
                                accumulator_citations = chunk.metadata.get("citations", accumulator_citations)
                                yield format_ui_sse({"type": chunk.type, "metadata": chunk.metadata})
                            elif chunk.type == "grounding" and chunk.metadata:
                                if "generated_images" in chunk.metadata and not accumulator_images:
                                    accumulator_images = chunk.metadata["generated_images"]
                                safe_grounding = _filter_unlocalized_images_meta(chunk.metadata)
                                if safe_grounding:
                                    accumulator_grounding = safe_grounding
                                    yield format_ui_sse({"type": "grounding", "metadata": safe_grounding})
                            elif chunk.type == "media" and chunk.metadata:
                                if "images" in chunk.metadata:
                                    accumulator_images = chunk.metadata["images"]
                                if "youtube" in chunk.metadata:
                                    accumulator_youtube = chunk.metadata["youtube"]
                                if "code_executions" in chunk.metadata:
                                    accumulator_code_executions = chunk.metadata["code_executions"]
                                safe_meta = _filter_unlocalized_images_meta(chunk.metadata)
                                if safe_meta:
                                    yield format_ui_sse({"type": "media", "metadata": safe_meta})
                        elif chunk.type == "done":
                            if fence_filter:
                                flushed = fence_filter.flush()
                                if flushed:
                                    text_emitted = True
                                    round_full_text.append(flushed)
                                    yield format_ui_sse({"type": "text", "delta": flushed})
                            done_meta = dict(chunk.metadata or {})
                            done_meta.update({
                                "fulfilled_by_account_id": fulfilled_by_id,
                                "fulfilled_by_account_name": fulfilled_by_name,
                                "failover_occurred": failover_occurred,
                                "was_failover": failover_occurred,
                                "failover_from_account": failover_from,
                                "original_account_id": original_account_id,
                            })
                            if done_meta.get("cid"):
                                done_cid = str(done_meta["cid"])
                            if done_meta.get("rid"):
                                done_rid = str(done_meta["rid"])
                            if done_meta.get("rcid"):
                                done_rcid = str(done_meta["rcid"])
                            if "citations" in done_meta and not accumulator_citations:
                                accumulator_citations = done_meta["citations"]
                            if "images" in done_meta and not accumulator_images:
                                accumulator_images = done_meta["images"]
                            if "youtube" in done_meta and not accumulator_youtube:
                                accumulator_youtube = done_meta["youtube"]
                            if "code_executions" in done_meta:
                                accumulator_code_executions = done_meta["code_executions"]
                            if "grounding" in done_meta and not accumulator_grounding:
                                accumulator_grounding = done_meta["grounding"]

                            # Detect MCP Tool Calling Block
                            candidate_text = done_meta.get("full_text") or "".join(raw_round_text)
                            m_tool = re.search(r"```tool_call\s*\n(.*?)\n```", candidate_text, re.DOTALL) if active_tools else None
                            if m_tool and tool_rounds < max_tool_rounds:
                                try:
                                    call_data = json.loads(m_tool.group(1).strip())
                                    t_name = call_data.get("name")
                                    t_args = call_data.get("arguments", {})
                                    conn_id = t_name.split("__", 1)[0] if "__" in t_name else ""
                                    bare_tool_name = t_name.split("__", 1)[1] if "__" in t_name else t_name

                                    # HITL Gate Evaluation
                                    from backend.app.mcp.approval_gate import get_approval_gate
                                    gate = get_approval_gate()
                                    conn_obj = get_workspace_store().get_connector(conn_id, mask=False)
                                    policy = conn_obj.approval_policy if conn_obj and conn_obj.approval_policy else "auto_allow_readonly"

                                    tool_meta = next((t for t in active_tools if t.get("namespaced_name") == t_name or t.get("name") == bare_tool_name), {})
                                    read_only_hint = tool_meta.get("read_only_hint")
                                    destructive_hint = tool_meta.get("destructive_hint")

                                    eval_res = gate.evaluate(
                                        connector_id=conn_id,
                                        tool_name=bare_tool_name,
                                        policy=policy,
                                        read_only_hint=read_only_hint,
                                        destructive_hint=destructive_hint,
                                    )

                                    decision = eval_res
                                    if eval_res == "ask":
                                        approval_id = f"apr_{uuid.uuid4().hex[:8]}"
                                        gate.create_pending(
                                            approval_id,
                                            connector_id=conn_id,
                                            tool_name=bare_tool_name,
                                            namespaced_name=t_name,
                                            arguments=t_args,
                                        )
                                        yield format_ui_sse({
                                            "type": "tool_approval_required",
                                            "metadata": {
                                                "approval_id": approval_id,
                                                "connector_id": conn_id,
                                                "tool_name": bare_tool_name,
                                                "namespaced_name": t_name,
                                                "arguments": t_args,
                                                "approval_policy": policy,
                                                "read_only_hint": read_only_hint,
                                                "destructive_hint": destructive_hint,
                                            },
                                        })
                                        decision = await gate.wait_for_decision(approval_id, timeout=120.0)

                                    if decision == "deny":
                                        rec = {
                                            "id": f"call_{uuid.uuid4().hex[:8]}",
                                            "name": t_name,
                                            "namespaced_name": t_name,
                                            "connector_id": conn_id,
                                            "tool_name": bare_tool_name,
                                            "arguments": t_args,
                                            "result": "Tool execution denied by user.",
                                            "is_error": True,
                                            "ok": False,
                                            "status": "denied",
                                            "duration_ms": 0,
                                        }
                                        recorded_tool_calls.append(rec)
                                        yield format_ui_sse({"type": "tool_call", "metadata": {"tool_calls": [rec]}})
                                        has_tool_call = True
                                        current_stream_prompt = (
                                            f"{effective_prompt}\n\n"
                                            f"Tool Execution `{t_name}` was denied by user.\n\n"
                                            "Please complete your response acknowledging the denial."
                                        )
                                        break

                                    from backend.app.api.workspace import get_mcp_manager
                                    tool_res = await get_mcp_manager().call_tool(t_name, t_args)
                                    is_err = bool(tool_res.get("is_error", False))
                                    rec = {
                                        "id": f"call_{uuid.uuid4().hex[:8]}",
                                        "name": t_name,
                                        "namespaced_name": t_name,
                                        "connector_id": conn_id,
                                        "tool_name": bare_tool_name,
                                        "arguments": t_args,
                                        "result": tool_res.get("result", ""),
                                        "is_error": is_err,
                                        "ok": not is_err,
                                        "status": "error" if is_err else "ok",
                                        "duration_ms": tool_res.get("duration_ms", 0),
                                    }
                                    recorded_tool_calls.append(rec)
                                    yield format_ui_sse({"type": "tool_call", "metadata": {"tool_calls": [rec]}})
                                    has_tool_call = True
                                    current_stream_prompt = (
                                        f"{effective_prompt}\n\n"
                                        f"Tool Execution `{t_name}` Result:\n{tool_res.get('result', '')}\n\n"
                                        "Please complete your response based on this tool result."
                                    )
                                    break
                                except Exception as t_err:
                                    logger.warning("Tool call execution error: %s", t_err)

                            if not round_full_text and done_meta.get("full_text"):
                                recovered_text = strip_tool_call_fences(done_meta["full_text"])
                                if recovered_text:
                                    round_full_text.append(recovered_text)
                                    text_emitted = True
                                    yield format_ui_sse({"type": "text", "delta": recovered_text})
                            full_text_list = round_full_text

                            img_dir = getattr(request.app.state, "image_data_dir", None)
                            if accumulator_images:
                                images_localized = await _localize_generated_images(
                                    accumulator_images,
                                    client_active,
                                    done_cid,
                                    done_rid,
                                    done_rcid,
                                    data_dir=img_dir,
                                )
                                if images_localized:
                                    yield format_ui_sse({
                                        "type": "media",
                                        "metadata": {
                                            "images": accumulator_images,
                                            "youtube": accumulator_youtube,
                                            **({"code_executions": accumulator_code_executions} if accumulator_code_executions else {}),
                                        },
                                    })
                            done_meta["images"] = accumulator_images
                            if accumulator_code_executions:
                                done_meta["code_executions"] = accumulator_code_executions
                            if recorded_tool_calls:
                                done_meta["tool_calls"] = recorded_tool_calls
                            if "full_text" in done_meta:
                                done_meta["full_text"] = strip_tool_call_fences(done_meta["full_text"])
                            yield format_ui_sse({"type": "done", "metadata": done_meta})

                    if not has_tool_call:
                        break
            except Exception as stream_err:
                err_code = getattr(stream_err, "code", None)
                err_str = str(stream_err)
                is_quota = (
                    err_code in (1037, 429, 1061)
                    or "1037" in err_str
                    or "429" in err_str
                    or "1061" in err_str
                    or "quota" in err_str.lower()
                    or "rate limit" in err_str.lower()
                )
                is_recoverable_bridge_err = isinstance(
                    stream_err,
                    (GeminiBridgeError, AuthenticationError, ModelInconsistentError),
                )
                if (is_quota or is_recoverable_bridge_err) and session_mgr and not text_emitted:
                    if is_quota:
                        session_mgr.mark_cooldown(
                            current_acc_id,
                            model_id=req.model,
                            duration_seconds=3600.0,
                            reason=err_str,
                        )
                    elif isinstance(stream_err, AuthenticationError):
                        session_mgr.mark_cooldown(
                            current_acc_id,
                            model_id=None,
                            duration_seconds=300.0,
                            reason=err_str,
                        )
                    backup = session_mgr.get_failover_account(
                        req.model,
                        current_acc_id,
                        required_tier=required_tier,
                    )
                    if backup:
                        if not is_quota and not isinstance(stream_err, AuthenticationError):
                            session_mgr.mark_cooldown(
                                current_acc_id,
                                model_id=req.model,
                                duration_seconds=120.0,
                                reason=err_str,
                            )
                        failover_occurred = True
                        failover_from = current_acc_id
                        fulfilled_by_id = backup.account_id
                        fulfilled_by_name = backup.profile.name
                        if is_quota:
                            code = (
                                1061
                                if (err_code == 1061 or "1061" in err_str)
                                else (429 if (err_code == 429 or "429" in err_str) else 1037)
                            )
                        else:
                            code = (
                                err_code
                                if isinstance(err_code, int)
                                else (7 if isinstance(stream_err, AuthenticationError) else 500)
                            )
                        yield format_ui_sse({
                            "type": "failover",
                            "from": orig_sess.profile.name if orig_sess and orig_sess.profile else current_acc_id,
                            "to": backup.profile.name,
                            "from_account_id": current_acc_id,
                            "to_account_id": backup.account_id,
                            "error_code": code,
                            "reason": err_str,
                            "model": req.model,
                            "timestamp": int(time.time()),
                        })
                        backup_cli = GeminiWebClient(
                            session_context=backup.context,
                            base_url=session_mgr.base_url,
                            vault=session_mgr.vault,
                            account_id=backup.account_id,
                        )
                        done_cid = None
                        done_rid = None
                        done_rcid = None
                        async for chunk in stream_with_disconnect_check(
                            run_stream(backup_cli, None, None, None),
                            request,
                            timeout=1.0,
                        ):
                            if chunk.type == "thought":
                                full_thought_list.append(chunk.delta)
                                yield format_ui_sse({"type": "thought", "delta": chunk.delta})
                            elif chunk.type == "text":
                                full_text_list.append(chunk.delta)
                                yield format_ui_sse({"type": "text", "delta": chunk.delta})
                            elif chunk.type in ("citation", "grounding", "media"):
                                if chunk.type == "citation" and chunk.metadata:
                                    accumulator_citations = chunk.metadata.get("citations", accumulator_citations)
                                    yield format_ui_sse({"type": chunk.type, "metadata": chunk.metadata})
                                elif chunk.type == "grounding" and chunk.metadata:
                                    if "generated_images" in chunk.metadata and not accumulator_images:
                                        accumulator_images = chunk.metadata["generated_images"]
                                    safe_grounding = _filter_unlocalized_images_meta(chunk.metadata)
                                    if safe_grounding:
                                        accumulator_grounding = safe_grounding
                                        yield format_ui_sse({"type": "grounding", "metadata": safe_grounding})
                                elif chunk.type == "media" and chunk.metadata:
                                    if "images" in chunk.metadata:
                                        accumulator_images = chunk.metadata["images"]
                                    if "youtube" in chunk.metadata:
                                        accumulator_youtube = chunk.metadata["youtube"]
                                    if "code_executions" in chunk.metadata:
                                        accumulator_code_executions = chunk.metadata["code_executions"]
                                    safe_meta = _filter_unlocalized_images_meta(chunk.metadata)
                                    if safe_meta:
                                        yield format_ui_sse({"type": "media", "metadata": safe_meta})
                            elif chunk.type == "done":
                                done_meta = dict(chunk.metadata or {})
                                done_meta.update({
                                    "fulfilled_by_account_id": fulfilled_by_id,
                                    "fulfilled_by_account_name": fulfilled_by_name,
                                    "failover_occurred": failover_occurred,
                                    "was_failover": failover_occurred,
                                    "failover_from_account": failover_from,
                                    "original_account_id": original_account_id,
                                    "cid": done_cid,
                                    "rid": done_rid,
                                    "rcid": done_rcid,
                                })
                                if done_meta.get("cid"):
                                    done_cid = str(done_meta["cid"])
                                if done_meta.get("rid"):
                                    done_rid = str(done_meta["rid"])
                                if done_meta.get("rcid"):
                                    done_rcid = str(done_meta["rcid"])
                                if "citations" in done_meta and not accumulator_citations:
                                    accumulator_citations = done_meta["citations"]
                                if "images" in done_meta and not accumulator_images:
                                    accumulator_images = done_meta["images"]
                                if "youtube" in done_meta and not accumulator_youtube:
                                    accumulator_youtube = done_meta["youtube"]
                                if "code_executions" in done_meta:
                                    accumulator_code_executions = done_meta["code_executions"]
                                if "grounding" in done_meta and not accumulator_grounding:
                                    accumulator_grounding = done_meta["grounding"]
                                if not full_text_list and done_meta.get("full_text"):
                                    recovered_text = done_meta["full_text"]
                                    full_text_list.append(recovered_text)
                                    text_emitted = True
                                    yield format_ui_sse({"type": "text", "delta": recovered_text})

                                img_dir = getattr(request.app.state, "image_data_dir", None)
                                if accumulator_images:
                                    images_localized = await _localize_generated_images(
                                        accumulator_images,
                                        backup_cli,
                                        done_cid,
                                        done_rid,
                                        done_rcid,
                                        data_dir=img_dir,
                                    )
                                    if images_localized:
                                        yield format_ui_sse({
                                            "type": "media",
                                            "metadata": {
                                                "images": accumulator_images,
                                                "youtube": accumulator_youtube,
                                                **({"code_executions": accumulator_code_executions} if accumulator_code_executions else {}),
                                            },
                                        })
                                done_meta["images"] = accumulator_images
                                if accumulator_code_executions:
                                    done_meta["code_executions"] = accumulator_code_executions
                                yield format_ui_sse({"type": "done", "metadata": done_meta})
                    else:
                        logger.error("Non-quota stream error: %s", stream_err, exc_info=True)
                        if not is_incognito and repo and user_msg_id is not None:
                            try:
                                await repo.add_message(
                                    conversation_id=conv_id,
                                    role="assistant",
                                    content=f"Error generating response: {stream_err}",
                                    parent_id=user_msg_id,
                                    thoughts="".join(full_thought_list) if full_thought_list else None,
                                    model=req.model,
                                    is_error=True,
                                    created_at=int(time.time() * 1000),
                                )
                            except Exception as e:
                                logger.error("Failed persisting error assistant message in repository: %s", e)
                        if db:
                            try:
                                dur_ms = int((time.time() - start_time) * 1000)
                                err_code_val = 1037 if "1037" in str(stream_err) else (429 if "429" in str(stream_err) else (1061 if "1061" in str(stream_err) else 500))
                                await db.record_telemetry(
                                    account_id=fulfilled_by_id,
                                    model_id=req.model,
                                    model_name=req.model,
                                    feature_mode="task" if is_task_or_research else "chat",
                                    status_code=err_code_val,
                                    error_code=str(stream_err),
                                    duration_ms=dur_ms,
                                    was_failover=1 if failover_occurred else 0,
                                    failover_from_account=failover_from,
                                )
                            except Exception:
                                pass
                        yield format_ui_sse({"type": "error", "error": f"{type(stream_err).__name__}: {stream_err}"})
                        return
                else:
                    logger.error("Stream error occurred: %s", stream_err, exc_info=True)
                    if not is_incognito and repo and user_msg_id is not None:
                        try:
                            await repo.add_message(
                                conversation_id=conv_id,
                                role="assistant",
                                content=f"Error generating response: {stream_err}",
                                parent_id=user_msg_id,
                                thoughts="".join(full_thought_list) if full_thought_list else None,
                                model=req.model,
                                is_error=True,
                                created_at=int(time.time() * 1000),
                            )
                        except Exception as e:
                            logger.error("Failed persisting error assistant message in repository: %s", e)
                    if db:
                        try:
                            dur_ms = int((time.time() - start_time) * 1000)
                            err_code_val = 1037 if "1037" in str(stream_err) else (429 if "429" in str(stream_err) else (1061 if "1061" in str(stream_err) else 500))
                            await db.record_telemetry(
                                account_id=fulfilled_by_id,
                                model_id=req.model,
                                model_name=req.model,
                                feature_mode="task" if is_task_or_research else "chat",
                                status_code=err_code_val,
                                error_code=str(stream_err),
                                duration_ms=dur_ms,
                                was_failover=1 if failover_occurred else 0,
                                failover_from_account=failover_from,
                            )
                        except Exception:
                            pass
                    yield format_ui_sse({"type": "error", "error": f"{type(stream_err).__name__}: {stream_err}"})
                    return

            full_text = strip_tool_call_fences("".join(full_text_list))
            full_thoughts = "".join(full_thought_list) if full_thought_list else None

            # Persist Assistant Turn in SQLite (bypassed if incognito)
            asst_msg_id = None
            if not is_incognito and repo and user_msg_id is not None:
                try:
                    rich_meta: dict[str, Any] = {
                        "citations": accumulator_citations,
                        "grounding": accumulator_grounding,
                        "images": accumulator_images,
                        "youtube": accumulator_youtube,
                        "fulfilled_by_account_id": fulfilled_by_id,
                    }
                    if accumulator_code_executions:
                        rich_meta["code_executions"] = accumulator_code_executions
                    if recorded_tool_calls:
                        rich_meta["tool_calls"] = recorded_tool_calls
                    if done_cid:
                        rich_meta["cid"] = done_cid
                    if done_rid:
                        rich_meta["rid"] = done_rid
                    if done_rcid:
                        rich_meta["rcid"] = done_rcid
                    asst_msg = await repo.add_message(
                        conversation_id=conv_id,
                        role="assistant",
                        content=full_text,
                        parent_id=user_msg_id,
                        thoughts=full_thoughts,
                        model=req.model,
                        metadata=rich_meta,
                        created_at=int(time.time() * 1000),
                    )
                    asst_msg_id = asst_msg.id
                    if done_cid:
                        await repo.update_conversation(conv_id, remote_conversation_id=done_cid)
                except Exception as e:
                    logger.error("Failed persisting assistant response in repository: %s", e)

            yield format_ui_sse({"type": "saved", "assistant_message_id": asst_msg_id})

            # Record Telemetry to SQLite
            if db:
                try:
                    p_tok = estimate_token_count(effective_prompt)
                    c_tok = estimate_token_count(full_text)
                    t_tok = estimate_token_count(full_thoughts or "")
                    dur_ms = int((time.time() - start_time) * 1000)
                    model_display = req.model
                    for m in DEFAULT_MODELS.values():
                        if m.id == req.model:
                            model_display = m.display_name
                            break
                    if req.feature_mode == "task":
                        model_display = "Task"

                    await db.record_telemetry(
                        account_id=fulfilled_by_id,
                        model_id=req.model,
                        model_name=model_display,
                        feature_mode="task" if is_task_or_research else "chat",
                        prompt_tokens_est=p_tok,
                        completion_tokens_est=c_tok,
                        thinking_tokens_est=t_tok,
                        status_code=200,
                        duration_ms=dur_ms,
                        was_failover=1 if failover_occurred else 0,
                        failover_from_account=failover_from,
                    )
                except Exception as tel_err:
                    logger.warning("Failed recording usage telemetry: %s", tel_err)

        except Exception as e:
            logger.error("Streaming error in generate_chat_turn: %s", e)
            if db:
                try:
                    dur_ms = int((time.time() - start_time) * 1000)
                    err_code_val = 1037 if "1037" in str(e) else (429 if "429" in str(e) else (1061 if "1061" in str(e) else 500))
                    await db.record_telemetry(
                        account_id=fulfilled_by_id,
                        model_id=req.model,
                        model_name=req.model,
                        feature_mode="task" if is_task_or_research else "chat",
                        status_code=err_code_val,
                        error_code=str(e),
                        duration_ms=dur_ms,
                        was_failover=1 if failover_occurred else 0,
                        failover_from_account=failover_from,
                    )
                except Exception:
                    pass

            if repo and user_msg_id is not None:
                with contextlib.suppress(Exception):
                    await repo.add_message(
                        conversation_id=conv_id,
                        role="assistant",
                        content=f"Error generating response: {e}",
                        parent_id=user_msg_id,
                        model=req.model,
                        is_error=True,
                        created_at=int(time.time() * 1000),
                    )
            yield format_ui_sse({"type": "error", "error": str(e)})
        finally:
            for cli in (client_active, client, backup_cli):
                if cli and hasattr(cli, "close"):
                    with contextlib.suppress(Exception):
                        await cli.close()

    return StreamingResponse(
        sse_event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
