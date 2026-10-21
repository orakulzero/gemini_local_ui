"""
backend/app/openai/routes.py
FastAPI router providing OpenAI-compatible /v1/models and /v1/chat/completions endpoints.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse

from backend.app.api.chat import stream_with_disconnect_check
from backend.app.bridge.client import GeminiWebClient
from backend.app.bridge.exceptions import (
    AuthenticationError,
    GeminiBridgeError,
    UsageLimitExceededError,
)
from backend.app.bridge.models import DEFAULT_MODELS, OPENAI_MODEL_ALIASES
from backend.app.bridge.uploader import GeminiUploader
from backend.app.openai import conversation_cache
from backend.app.openai.adapter import (
    InvalidImagePayloadError,
    ModelNotFoundError,
    OpenAIAdapter,
)
from backend.app.openai.image_store import link_images, save_image
from backend.app.openai.schemas import (
    ChatCompletionRequest,
    OpenAIErrorDetail,
    OpenAIErrorResponse,
    OpenAIModel,
    OpenAIModelList,
)
from backend.app.openai.sse import (
    format_non_streaming_response,
    generate_completion_id,
    stream_openai_sse,
)

logger = logging.getLogger("gemini_openai.routes")

router = APIRouter(prefix="/v1", tags=["OpenAI Compatibility"])


def estimate_token_count(text: str) -> int:
    """Estimates tokens using calibrated multi-lingual sentencepiece heuristics."""
    if not text:
        return 0
    cyrillic_chars = sum(1 for c in text if "\u0400" <= c <= "\u04FF")
    if cyrillic_chars > len(text) * 0.3:
        return max(1, int(len(text) / 1.8))
    return max(1, int(len(text) / 3.8))


def make_openai_error(
    message: str,
    status_code: int = 400,
    param: Optional[str] = None,
    code: Optional[str] = None,
    error_type: str = "invalid_request_error",
) -> JSONResponse:
    """Builds a standard OpenAI-formatted error response."""
    payload = OpenAIErrorResponse(
        error=OpenAIErrorDetail(
            message=message,
            type=error_type,
            param=param,
            code=code,
        )
    )
    return JSONResponse(status_code=status_code, content=payload.model_dump())


def get_gemini_client(request: Request) -> GeminiWebClient:
    """Dependency injection resolving active GeminiWebClient with X-Gemini-Account routing."""
    session_mgr = getattr(request.app.state, "session_manager", None)
    if not session_mgr:
        import backend.app.main as main_mod
        session_mgr = getattr(main_mod, "session_manager", None)

    if not session_mgr:
        raise HTTPException(status_code=500, detail="Gemini Session Manager is not initialized")

    account_id = request.headers.get("x-gemini-account")
    ctx = session_mgr.get_session_context(account_id)
    return GeminiWebClient(session_context=ctx, base_url=session_mgr.base_url)


def get_gemini_uploader(request: Request) -> GeminiUploader:
    """Dependency injection resolving active GeminiUploader with X-Gemini-Account routing."""
    session_mgr = getattr(request.app.state, "session_manager", None)
    if not session_mgr:
        import backend.app.main as main_mod
        session_mgr = getattr(main_mod, "session_manager", None)

    if not session_mgr:
        raise HTTPException(status_code=500, detail="Gemini Session Manager is not initialized")

    account_id = request.headers.get("x-gemini-account")
    ctx = session_mgr.get_session_context(account_id)
    return GeminiUploader(session_context=ctx, base_url=session_mgr.base_url)


@router.post("/files")
async def openai_upload_file(
    request: Request,
    file: UploadFile = File(...),
    purpose: str = Form("assistants"),
    uploader: GeminiUploader = Depends(get_gemini_uploader),
) -> Any:
    """Uploads file or archive and returns OpenAI-compatible file object."""
    content = await file.read()
    filename = file.filename or "file"
    mime_type = file.content_type or GeminiUploader.detect_mime_type(filename)
    try:
        uri = await uploader.upload_file(data=content, filename=filename, mime_type=mime_type)
        return {
            "id": uri,
            "object": "file",
            "bytes": len(content),
            "created_at": int(time.time()),
            "filename": filename,
            "purpose": purpose,
            "status": "processed",
        }
    except Exception as e:
        logger.error("Failed uploading file via /v1/files: %s", e)
        return make_openai_error(f"Upload failed: {e}", status_code=500)


@router.get("/models", response_model=OpenAIModelList)
async def list_models(
    client: GeminiWebClient = Depends(get_gemini_client),
) -> OpenAIModelList:
    """Lists available canonical models, aliases, and active Custom Gems."""
    models_data: List[OpenAIModel] = []
    seen_ids: set[str] = set()

    # 1. Canonical Gemini Models
    for model_info in DEFAULT_MODELS.values():
        if model_info.id not in seen_ids:
            seen_ids.add(model_info.id)
            models_data.append(
                OpenAIModel(
                    id=model_info.id,
                    root=model_info.id,
                    owned_by="google-gemini",
                )
            )

    # 2. OpenAI Compatibility Aliases
    for alias_id in OPENAI_MODEL_ALIASES.keys():
        if alias_id not in seen_ids:
            seen_ids.add(alias_id)
            models_data.append(
                OpenAIModel(
                    id=alias_id,
                    root=alias_id,
                    owned_by="google-gemini-alias",
                )
            )

    # 3. Custom Gems
    try:
        gems = await client.list_gems()
        for gem in gems:
            gem_model_id = f"gem/{gem.id}"
            if gem_model_id not in seen_ids:
                seen_ids.add(gem_model_id)
                models_data.append(
                    OpenAIModel(
                        id=gem_model_id,
                        root=gem_model_id,
                        owned_by="google-gemini-gem",
                        created=gem.created_at or 1700000000,
                    )
                )
    except Exception as e:
        logger.debug("Could not list remote gems for models catalog: %s", e)

    return OpenAIModelList(data=models_data)


@router.post("/chat/completions")
async def chat_completions(
    req: ChatCompletionRequest,
    request: Request,
    client: GeminiWebClient = Depends(get_gemini_client),
    uploader: GeminiUploader = Depends(get_gemini_uploader),
) -> Response:
    """
    Standard OpenAI Chat Completions handler supporting both streaming (SSE)
    and buffered non-streaming responses, system prompts, multimodal vision,
    and automatic quota failover.
    """
    session_mgr = getattr(request.app.state, "session_manager", None)
    if not session_mgr:
        import backend.app.main as main_mod
        session_mgr = getattr(main_mod, "session_manager", None)

    repo = getattr(request.app.state, "repository", None)
    if not repo:
        import backend.app.main as main_mod
        repo = getattr(main_mod, "repository", None)

    db = getattr(request.app.state, "db", None) or getattr(request.app.state, "database", None)
    if not db:
        import backend.app.main as main_mod
        db = getattr(main_mod, "db", None)

    # Target account determination
    target_account_id = request.headers.get("x-gemini-account") or req.account_id or (session_mgr.active_account_id if session_mgr else "default")
    if session_mgr and get_gemini_client not in request.app.dependency_overrides:
        ctx = session_mgr.get_session_context(target_account_id)
        client = GeminiWebClient(session_context=ctx, base_url=session_mgr.base_url)

    # 1. Validation: Messages
    if not req.messages:
        return make_openai_error("messages cannot be empty", status_code=400, param="messages")

    # 2. Validation: Model Resolution
    try:
        canonical_model, gem_id = OpenAIAdapter.resolve_model(req.model)
        if not gem_id and getattr(req, "gem_id", None):
            gem_id = req.gem_id
        if getattr(req, "feature_mode", None) in ("task", "tasks", "40", "spark") and "@mode=" not in canonical_model:
            canonical_model = f"{canonical_model}@mode=task"
    except ModelNotFoundError as e:
        return make_openai_error(
            str(e),
            status_code=404,
            param="model",
            code="model_not_found",
        )

    # 3. Conversation Prefix Cache Lookup & Extraction
    has_tools = bool(req.tools and req.tool_choice != "none")
    matched_n, cached_chat = conversation_cache.find(req.messages)
    resume_cid: Optional[str] = None
    resume_rid: Optional[str] = None
    resume_rcid: Optional[str] = None

    try:
        if cached_chat and matched_n > 0:
            resume_cid = cached_chat.get("cid") or None
            resume_rid = cached_chat.get("rid") or None
            resume_rcid = cached_chat.get("rcid") or None
            delta_messages = req.messages[matched_n:]
            delta_sys = OpenAIAdapter.extract_system_instruction(delta_messages)
            prompt, delta_history, image_descriptors = await OpenAIAdapter.extract_turns_and_attachments(
                delta_messages, system_instruction=delta_sys
            )
            if delta_history:
                hist_lines = [f"[{h['role']}]: {h['content']}" for h in delta_history if h.get("content")]
                if hist_lines:
                    prompt = "\n\n".join(hist_lines + ([prompt] if prompt else []))
            history = None
        else:
            system_inst = OpenAIAdapter.extract_system_instruction(
                req.messages,
                req.response_format,
                tools=req.tools,
                tool_choice=req.tool_choice,
            )
            prompt, history, image_descriptors = await OpenAIAdapter.extract_turns_and_attachments(
                req.messages, system_instruction=system_inst
            )
    except InvalidImagePayloadError as e:
        return make_openai_error(str(e), status_code=400, param="messages")

    # 5. Upload Attachments
    attachments: List[Dict[str, str]] = []
    if image_descriptors:
        try:
            attachments = await OpenAIAdapter.stage_and_upload_images(image_descriptors, uploader)
        except AuthenticationError:
            return make_openai_error("Session credentials expired during attachment upload", status_code=401)
        except Exception as e:
            logger.error("Attachment upload failure: %s", e)
            return make_openai_error(f"Failed uploading image attachment: {e}", status_code=500)

    # 6. Parse Stop Sequences
    stop_seqs: Optional[List[str]] = None
    if req.stop:
        if isinstance(req.stop, str):
            stop_seqs = [req.stop]
        elif isinstance(req.stop, list):
            stop_seqs = [s for s in req.stop if isinstance(s, str)]

    request_id = generate_completion_id()
    created_at = int(time.time())
    start_time = time.time()
    failover_occurred = False
    failover_from: Optional[str] = None
    fulfilled_by_id = target_account_id
    done_metadata: Dict[str, Any] = {}

    def _build_stream_kwargs() -> Dict[str, Any]:
        kw: Dict[str, Any] = {
            "prompt": prompt,
            "model": canonical_model,
            "gem_id": gem_id,
            "attachments": attachments if attachments else None,
            "history": history if history else None,
            "extensions": getattr(req, "extensions", None),
        }
        if resume_cid:
            kw["cid"] = resume_cid
            kw["rid"] = resume_rid
            kw["rcid"] = resume_rcid
        return kw

    def _remember_turn(reply_text: str) -> None:
        cid_val = done_metadata.get("cid") or resume_cid
        if not cid_val:
            return
        chat_state = {
            "cid": str(cid_val),
            "rid": str(done_metadata.get("rid") or resume_rid or ""),
            "rcid": str(done_metadata.get("rcid") or resume_rcid or ""),
        }
        conversation_cache.remember(req.messages, reply_text, chat_state)
        if has_tools or "```tool_call" in reply_text:
            clean_t, t_calls = OpenAIAdapter.parse_tool_calls(reply_text)
            if t_calls:
                conversation_cache.remember(
                    req.messages,
                    {
                        "role": "assistant",
                        "content": clean_t if clean_t else None,
                        "tool_calls": t_calls,
                    },
                    chat_state,
                )

    # Helper for failover streaming
    async def create_stream_with_failover():
        nonlocal client, target_account_id, failover_occurred, failover_from, fulfilled_by_id, done_metadata
        current_acc_id = target_account_id
        required_tier = "pro" if (canonical_model in ("95a4c0abcba24364", "e6fa609c3fa255c0") or "pro" in req.model.lower()) else "free"

        # Check pre-flight cooldown
        if session_mgr and session_mgr.is_in_cooldown(current_acc_id, model_id=canonical_model):
            backup = session_mgr.get_failover_account(canonical_model, current_acc_id, required_tier=required_tier)
            if backup:
                failover_occurred = True
                failover_from = current_acc_id
                fulfilled_by_id = backup.account_id
                client = GeminiWebClient(session_context=backup.context, base_url=session_mgr.base_url)
                current_acc_id = backup.account_id

        stream_kw = _build_stream_kwargs()
        try:
            async for chunk in client.generate_stream(**stream_kw):
                if chunk.type == "done" and chunk.metadata:
                    done_metadata.update(chunk.metadata)
                yield chunk
        except Exception as err:
            err_str = str(err)
            is_quota = (
                isinstance(err, UsageLimitExceededError)
                or "1037" in err_str
                or "429" in err_str
                or "1061" in err_str
            )
            if is_quota and session_mgr:
                session_mgr.mark_cooldown(current_acc_id, model_id=canonical_model, duration_seconds=3600.0, reason=err_str)
                backup = session_mgr.get_failover_account(canonical_model, current_acc_id, required_tier=required_tier)
                if backup:
                    logger.info("OpenAI proxy failing over from %s to %s due to %s", current_acc_id, backup.account_id, err)
                    failover_occurred = True
                    failover_from = current_acc_id
                    fulfilled_by_id = backup.account_id
                    backup_client = GeminiWebClient(session_context=backup.context, base_url=session_mgr.base_url)
                    async for chunk in backup_client.generate_stream(**stream_kw):
                        if chunk.type == "done" and chunk.metadata:
                            done_metadata.update(chunk.metadata)
                        yield chunk
                    return
            raise

    async def log_telemetry(status_code: int = 200, full_resp_text: str = "", error_str: Optional[str] = None) -> None:
        try:
            dur_ms = int((time.time() - start_time) * 1000)
            p_tok = estimate_token_count(prompt)
            c_tok = estimate_token_count(full_resp_text)
            model_display = req.model
            for m in DEFAULT_MODELS.values():
                if m.id == canonical_model:
                    model_display = m.display_name
                    break

            if repo and hasattr(repo, "log_usage_telemetry"):
                await repo.log_usage_telemetry(
                    account_id=fulfilled_by_id,
                    model_id=canonical_model,
                    model_name=model_display,
                    feature_mode="chat",
                    prompt_tokens_est=p_tok,
                    completion_tokens_est=c_tok,
                    status_code=status_code,
                    error_code=error_str,
                    duration_ms=dur_ms,
                    was_failover=1 if failover_occurred else 0,
                    failover_from_account=failover_from,
                )
            elif db and hasattr(db, "record_telemetry"):
                await db.record_telemetry(
                    account_id=fulfilled_by_id,
                    model_id=canonical_model,
                    model_name=model_display,
                    feature_mode="chat",
                    prompt_tokens_est=p_tok,
                    completion_tokens_est=c_tok,
                    status_code=status_code,
                    error_code=error_str,
                    duration_ms=dur_ms,
                    was_failover=1 if failover_occurred else 0,
                    failover_from_account=failover_from,
                )
        except Exception as tel_err:
            logger.warning("Failed logging usage telemetry in OpenAI proxy: %s", tel_err)

    # 7. Execution: Streaming vs Non-Streaming
    try:
        if req.stream:
            accumulated_chunks: List[str] = []

            async def tracking_stream_generator():
                nonlocal accumulated_chunks
                try:
                    protected_stream = stream_with_disconnect_check(
                        create_stream_with_failover(),
                        request,
                        timeout=1.0,
                        max_queue_size=64,
                    )
                    async for chunk in protected_stream:
                        if chunk.type == "text" and chunk.delta:
                            accumulated_chunks.append(chunk.delta)
                        yield chunk
                except Exception as stream_err:
                    err_code = 1037 if "1037" in str(stream_err) else (429 if "429" in str(stream_err) else 500)
                    await log_telemetry(status_code=err_code, full_resp_text="".join(accumulated_chunks), error_str=str(stream_err))
                    raise

            async def tracked_sse_response():
                try:
                    async for line in stream_openai_sse(
                        stream_generator=tracking_stream_generator(),
                        request_id=request_id,
                        model_name=req.model,
                        created_at=created_at,
                        stop_sequences=stop_seqs,
                        has_tools=has_tools,
                    ):
                        yield line
                    final_streamed_text = "".join(accumulated_chunks)
                    _remember_turn(final_streamed_text)
                    await log_telemetry(status_code=200, full_resp_text=final_streamed_text)
                finally:
                    if client and hasattr(client, "close"):
                        try:
                            await client.close()
                        except Exception:
                            pass

            return StreamingResponse(
                tracked_sse_response(),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "X-Accel-Buffering": "no",
                },
            )
        else:
            full_text_chunks: List[str] = []
            try:
                gen = create_stream_with_failover()
                try:
                    async for chunk in gen:
                        if chunk.type == "text" and chunk.delta:
                            full_text_chunks.append(chunk.delta)
                        elif chunk.type == "done":
                            break
                finally:
                    if hasattr(gen, "aclose"):
                        try:
                            await gen.aclose()
                        except Exception:
                            pass
            except Exception as stream_err:
                err_code = 1037 if "1037" in str(stream_err) else (429 if "429" in str(stream_err) else 500)
                await log_telemetry(status_code=err_code, full_resp_text="".join(full_text_chunks), error_str=str(stream_err))
                raise
            finally:
                if client and hasattr(client, "close"):
                    try:
                        await client.close()
                    except Exception:
                        pass

            full_text = "".join(full_text_chunks)

            # Apply stop sequence truncation if applicable
            if stop_seqs:
                for s in stop_seqs:
                    if s in full_text:
                        full_text = full_text[:full_text.find(s)]
                        break

            # Rewrite or append generated images if present in done_metadata
            gen_images = done_metadata.get("images")
            if isinstance(gen_images, list) and gen_images:
                for img_item in gen_images:
                    if isinstance(img_item, dict) and not img_item.get("file") and isinstance(img_item.get("data"), (bytes, bytearray)):
                        img_item["file"] = save_image(bytes(img_item["data"]), str(img_item.get("mime") or "image/png"))
                base_u = str(request.base_url).rstrip("/") if request.base_url else ""
                full_text = link_images(full_text, gen_images, base_url=base_u)

            _remember_turn(full_text)
            await log_telemetry(status_code=200, full_resp_text=full_text)

            resp_obj = format_non_streaming_response(
                full_text=full_text,
                request_id=request_id,
                model_name=req.model,
                created_at=created_at,
                prompt_text=prompt,
                has_tools=has_tools,
            )
            return JSONResponse(status_code=200, content=resp_obj.model_dump())

    except AuthenticationError as e:
        return make_openai_error(f"Authentication failure: {e}", status_code=401, code="invalid_api_key")
    except GeminiBridgeError as e:
        return make_openai_error(f"Upstream Gemini error: {e}", status_code=502, code="upstream_error")
    except Exception as e:
        logger.exception("Unexpected error in chat_completions: %s", e)
        return make_openai_error(f"Internal server error: {e}", status_code=500)
