"""
backend/app/api/chat.py
Local Web UI SSE streaming generation endpoint with SQLite persistence.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple, Union
import uuid
from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from backend.app.bridge.client import GeminiWebClient
from backend.app.bridge.models import DEFAULT_MODELS
from backend.app.storage.database import Database
from backend.app.storage.repository import ConversationRepository

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
    queue: asyncio.Queue[Tuple[bool, Any]] = asyncio.Queue(maxsize=max_queue_size)
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
    uri: Optional[str] = None
    filename: str
    mime_type: str = "application/octet-stream"
    file_size: int = 0
    storage_path: Optional[str] = None


class ChatGenerateRequest(BaseModel):
    conversation_id: Optional[str] = None
    parent_message_id: Optional[Union[int, str]] = None
    existing_user_message_id: Optional[int] = None
    prompt: str = ""
    model: str = "gemini-2.5-flash"
    gem_id: Optional[str] = None
    attachments: Optional[List[ChatAttachmentInput]] = None
    account_id: Optional[str] = None
    feature_mode: Optional[str] = "chat"
    incognito: bool = False
    system_instruction: Optional[str] = None


def format_ui_sse(event_data: Dict[str, Any]) -> str:
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


def get_repository(request: Request) -> Optional[ConversationRepository]:
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


@router.post("/upload")
async def upload_chat_attachment(
    request: Request,
    file: UploadFile = File(...),
) -> Dict[str, Any]:
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
    mime_type = file.content_type or GeminiUploader.detect_mime_type(filename)

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


@router.post("/generate")
async def generate_chat_turn(
    req: ChatGenerateRequest,
    request: Request,
    client: GeminiWebClient = Depends(get_gemini_client),
    repo: Optional[ConversationRepository] = Depends(get_repository),
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
    effective_prompt = req.prompt
    bridge_attachments = None
    if req.attachments:
        bridge_attachments = [
            {"uri": a.uri, "filename": a.filename}
            for a in req.attachments
            if a.uri
        ]

    if req.existing_user_message_id is not None and repo:
        existing_msg = await repo.get_message(int(req.existing_user_message_id))
        if existing_msg:
            if not effective_prompt.strip():
                effective_prompt = existing_msg.content
            if not req.attachments and existing_msg.attachments:
                bridge_attachments = [
                    {"uri": a.uri, "filename": a.filename}
                    for a in existing_msg.attachments
                    if a.uri
                ]

    # Prepend Turn-0 system persona prefix when system_instruction is set without server-side Gem
    if req.system_instruction and not req.gem_id:
        effective_prompt = (
            f"[System Instructions]\n"
            f"{req.system_instruction.strip()}\n"
            f"[End System Instructions]\n\n"
            f"{effective_prompt}"
        )

    if not effective_prompt.strip() and not req.attachments and not bridge_attachments:
        raise HTTPException(status_code=400, detail="Prompt or attachments must be provided")

    conv_id = req.conversation_id or f"conv_{uuid.uuid4().hex[:12]}"
    user_msg_id: Optional[int] = req.existing_user_message_id
    now_ms = int(time.time() * 1000)

    # 1. Persist User Message in SQLite (unless incognito or continuing from an already-persisted message)
    if not is_incognito and repo and user_msg_id is None:
        try:
            existing_conv = await repo.get_conversation(conv_id)
            if not existing_conv:
                await repo.create_conversation(
                    title=effective_prompt[:50] if effective_prompt else "New Conversation",
                    model=req.model,
                    gem_id=req.gem_id,
                    conversation_id=conv_id,
                )

            user_msg = await repo.add_message(
                conversation_id=conv_id,
                role="user",
                content=effective_prompt,
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
            {"uri": a.uri, "filename": a.filename}
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
        full_text_list: List[str] = []
        full_thought_list: List[str] = []
        text_emitted = False

        current_acc_id = target_account_id
        original_account_id = target_account_id
        failover_occurred = False
        failover_from = None

        orig_sess = session_mgr.get_account_session(current_acc_id) if session_mgr else None
        fulfilled_by_id = current_acc_id
        fulfilled_by_name = orig_sess.profile.name if orig_sess and orig_sess.profile else current_acc_id

        # Determine model tier requirements
        is_high_tier_mode = (
            req.model in ("95a4c0abcba24364", "e6fa609c3fa255c0")
            or "pro" in req.model.lower()
            or "argon" in req.model.lower()
            or req.feature_mode == "task"
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
        if req.feature_mode in ("task", "tasks", "40", "spark") and "@mode=" not in effective_model:
            effective_model = f"{effective_model}@mode=task"

        async def run_stream(active_cli: GeminiWebClient) -> AsyncIterator[Any]:
            async for chk in active_cli.generate_stream(
                prompt=effective_prompt,
                model=effective_model,
                gem_id=req.gem_id,
                attachments=bridge_attachments,
                incognito=is_incognito,
                feature_mode=40 if req.feature_mode in ("task", "tasks", "40", "spark") else None,
            ):
                yield chk

        accumulator_citations: List[Any] = []
        accumulator_images: List[Any] = []
        accumulator_grounding: Dict[str, Any] = {}
        accumulator_youtube: List[Any] = []

        try:
            try:
                async for chunk in stream_with_disconnect_check(run_stream(client_active), request, timeout=1.0):
                    if chunk.type == "thought":
                        full_thought_list.append(chunk.delta)
                        yield format_ui_sse({"type": "thought", "delta": chunk.delta})
                    elif chunk.type == "text":
                        text_emitted = True
                        full_text_list.append(chunk.delta)
                        yield format_ui_sse({"type": "text", "delta": chunk.delta})
                    elif chunk.type in ("citation", "grounding", "media"):
                        if chunk.type == "citation" and chunk.metadata:
                            accumulator_citations = chunk.metadata.get("citations", accumulator_citations)
                        elif chunk.type == "grounding" and chunk.metadata:
                            accumulator_grounding = chunk.metadata
                            if "generated_images" in chunk.metadata and not accumulator_images:
                                accumulator_images = chunk.metadata["generated_images"]
                        elif chunk.type == "media" and chunk.metadata:
                            if "images" in chunk.metadata:
                                accumulator_images = chunk.metadata["images"]
                            if "youtube" in chunk.metadata:
                                accumulator_youtube = chunk.metadata["youtube"]
                        yield format_ui_sse({"type": chunk.type, "metadata": chunk.metadata})
                    elif chunk.type == "done":
                        done_meta = dict(chunk.metadata or {})
                        done_meta.update({
                            "fulfilled_by_account_id": fulfilled_by_id,
                            "fulfilled_by_account_name": fulfilled_by_name,
                            "failover_occurred": failover_occurred,
                            "was_failover": failover_occurred,
                            "failover_from_account": failover_from,
                            "original_account_id": original_account_id,
                        })
                        if "citations" in done_meta and not accumulator_citations:
                            accumulator_citations = done_meta["citations"]
                        if "images" in done_meta and not accumulator_images:
                            accumulator_images = done_meta["images"]
                        if "youtube" in done_meta and not accumulator_youtube:
                            accumulator_youtube = done_meta["youtube"]
                        if "grounding" in done_meta and not accumulator_grounding:
                            accumulator_grounding = done_meta["grounding"]
                        if not full_text_list and done_meta.get("full_text"):
                            recovered_text = done_meta["full_text"]
                            full_text_list.append(recovered_text)
                            text_emitted = True
                            yield format_ui_sse({"type": "text", "delta": recovered_text})
                        yield format_ui_sse({"type": "done", "metadata": done_meta})
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
                if is_quota and session_mgr and not text_emitted:
                    session_mgr.mark_cooldown(current_acc_id, model_id=req.model, duration_seconds=3600.0, reason=err_str)
                    backup = session_mgr.get_failover_account(req.model, current_acc_id, required_tier=required_tier)
                    if backup:
                        failover_occurred = True
                        failover_from = current_acc_id
                        fulfilled_by_id = backup.account_id
                        fulfilled_by_name = backup.profile.name
                        code = 1061 if (err_code == 1061 or "1061" in err_str) else (429 if (err_code == 429 or "429" in err_str) else 1037)
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
                        async for chunk in stream_with_disconnect_check(run_stream(backup_cli), request, timeout=1.0):
                            if chunk.type == "thought":
                                full_thought_list.append(chunk.delta)
                                yield format_ui_sse({"type": "thought", "delta": chunk.delta})
                            elif chunk.type == "text":
                                full_text_list.append(chunk.delta)
                                yield format_ui_sse({"type": "text", "delta": chunk.delta})
                            elif chunk.type in ("citation", "grounding", "media"):
                                if chunk.type == "citation" and chunk.metadata:
                                    accumulator_citations = chunk.metadata.get("citations", accumulator_citations)
                                elif chunk.type == "grounding" and chunk.metadata:
                                    accumulator_grounding = chunk.metadata
                                    if "generated_images" in chunk.metadata and not accumulator_images:
                                        accumulator_images = chunk.metadata["generated_images"]
                                elif chunk.type == "media" and chunk.metadata:
                                    if "images" in chunk.metadata:
                                        accumulator_images = chunk.metadata["images"]
                                    if "youtube" in chunk.metadata:
                                        accumulator_youtube = chunk.metadata["youtube"]
                                yield format_ui_sse({"type": chunk.type, "metadata": chunk.metadata})
                            elif chunk.type == "done":
                                done_meta = dict(chunk.metadata or {})
                                done_meta.update({
                                    "fulfilled_by_account_id": fulfilled_by_id,
                                    "fulfilled_by_account_name": fulfilled_by_name,
                                    "failover_occurred": failover_occurred,
                                    "was_failover": failover_occurred,
                                    "failover_from_account": failover_from,
                                    "original_account_id": original_account_id,
                                })
                                if "citations" in done_meta and not accumulator_citations:
                                    accumulator_citations = done_meta["citations"]
                                if "images" in done_meta and not accumulator_images:
                                    accumulator_images = done_meta["images"]
                                if "youtube" in done_meta and not accumulator_youtube:
                                    accumulator_youtube = done_meta["youtube"]
                                if "grounding" in done_meta and not accumulator_grounding:
                                    accumulator_grounding = done_meta["grounding"]
                                if not full_text_list and done_meta.get("full_text"):
                                    recovered_text = done_meta["full_text"]
                                    full_text_list.append(recovered_text)
                                    text_emitted = True
                                    yield format_ui_sse({"type": "text", "delta": recovered_text})
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
                                    feature_mode=req.feature_mode or "chat",
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
                                feature_mode=req.feature_mode or "chat",
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

            full_text = "".join(full_text_list)
            full_thoughts = "".join(full_thought_list) if full_thought_list else None

            # Persist Assistant Turn in SQLite (bypassed if incognito)
            asst_msg_id = None
            if not is_incognito and repo and user_msg_id is not None:
                try:
                    rich_meta = {
                        "citations": accumulator_citations,
                        "grounding": accumulator_grounding,
                        "images": accumulator_images,
                        "youtube": accumulator_youtube,
                    }
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
                        feature_mode=req.feature_mode or "chat",
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
                        feature_mode=req.feature_mode or "chat",
                        status_code=err_code_val,
                        error_code=str(e),
                        duration_ms=dur_ms,
                        was_failover=1 if failover_occurred else 0,
                        failover_from_account=failover_from,
                    )
                except Exception:
                    pass

            if repo and user_msg_id is not None:
                try:
                    await repo.add_message(
                        conversation_id=conv_id,
                        role="assistant",
                        content=f"Error generating response: {e}",
                        parent_id=user_msg_id,
                        model=req.model,
                        is_error=True,
                        created_at=int(time.time() * 1000),
                    )
                except Exception:
                    pass
            yield format_ui_sse({"type": "error", "error": str(e)})
        finally:
            for cli in (client_active, client, backup_cli):
                if cli and hasattr(cli, "close"):
                    try:
                        await cli.close()
                    except Exception:
                        pass

    return StreamingResponse(
        sse_event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
