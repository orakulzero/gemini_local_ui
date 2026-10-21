"""
backend/app/anthropic/routes.py
FastAPI router for Anthropic Messages API v1:
- POST /v1/messages (streaming SSE and buffered JSON)
- POST /v1/messages/count_tokens
Supports multi-turn stateful continuation via SHA-256 conversation prefix cache,
Claude-to-Gemini model mapping, extended thinking blocks, and bidirectional tool calling.
"""

from __future__ import annotations

import logging
import time
from typing import Any, AsyncIterator, Dict, List, Optional, Union
from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse, StreamingResponse

from backend.app.anthropic.adapter import AnthropicAdapter
from backend.app.api.claude_plugins import detect_subagent_profile
from backend.app.anthropic.schemas import (
    ContentBlock,
    CountTokensRequest,
    CountTokensResponse,
    MessageParam,
    MessageRequest,
    MessageResponse,
    MessageUsage,
    TextBlock,
    ThinkingBlock,
    ToolUseBlock,
)
from backend.app.anthropic.sse import (
    generate_message_id,
    stream_anthropic_sse,
)
from backend.app.bridge.client import GeminiWebClient
from backend.app.bridge.models import DEFAULT_MODELS
from backend.app.bridge.session import SessionHealthStatus, SessionManager
from backend.app.bridge.streaming_parser import StreamChunk
from backend.app.bridge.uploader import GeminiUploader
from backend.app.openai import conversation_cache
from backend.app.openai.adapter import (
    ImageAttachmentDescriptor,
    InvalidImagePayloadError,
    ModelNotFoundError,
)
from backend.app.openai.routes import stream_with_disconnect_check

logger = logging.getLogger("gemini_anthropic.routes")

router = APIRouter(tags=["Anthropic"])


def make_anthropic_error(
    message: str,
    error_type: str = "invalid_request_error",
    status_code: int = 400,
) -> JSONResponse:
    """Constructs official Anthropic JSON error envelope."""
    return JSONResponse(
        status_code=status_code,
        content={
            "type": "error",
            "error": {
                "type": error_type,
                "message": message,
            },
        },
    )


def get_gemini_client(request: Request) -> Optional[GeminiWebClient]:
    """Dependency injection resolving active GeminiWebClient."""
    from backend.app.openai.routes import get_gemini_client as resolve_client
    try:
        return resolve_client(request)
    except Exception as e:
        logger.debug("Optional GeminiWebClient not available: %s", e)
        return None


def get_gemini_uploader(request: Request) -> Optional[GeminiUploader]:
    """Dependency injection resolving active GeminiUploader."""
    from backend.app.openai.routes import get_gemini_uploader as resolve_uploader
    try:
        return resolve_uploader(request)
    except Exception as e:
        logger.debug("Optional GeminiUploader not available: %s", e)
        return None


@router.post("/messages", response_model=None)
@router.post("/v1/messages", response_model=None, include_in_schema=False)
async def create_anthropic_message(
    req: MessageRequest,
    request: Request,
    response: Response,
    client: Optional[GeminiWebClient] = Depends(get_gemini_client),
    uploader: Optional[GeminiUploader] = Depends(get_gemini_uploader),
    x_gemini_account: Optional[str] = Header(None),
) -> Any:
    """
    Anthropic Messages API v1 endpoint.
    Translates input messages, tools, and system prompt into Gemini Web calls,
    leveraging SHA-256 conversation caching and automatic account failover.
    """
    # 1. Validation: Messages
    if not req.messages:
        return make_anthropic_error("messages: array must not be empty", error_type="invalid_request_error", status_code=400)

    # 2. Model Resolution & Per-Subagent Tier / Custom Gem Routing
    subagent_profile = detect_subagent_profile(req.system)
    subagent_feature_mode: Optional[int] = None
    try:
        canonical_model, gem_id = AnthropicAdapter.resolve_model(req.model)
        if not gem_id and getattr(req, "gem_id", None):
            gem_id = req.gem_id
        if subagent_profile:
            canonical_model = subagent_profile["canonical_model"]
            if not gem_id and subagent_profile.get("gem_id"):
                gem_id = subagent_profile["gem_id"]
            subagent_feature_mode = subagent_profile.get("feature_mode")
    except ModelNotFoundError as e:
        return make_anthropic_error(str(e), error_type="not_found_error", status_code=404)

    # 3. Account & Client Resolution
    session_mgr: Optional[SessionManager] = getattr(request.app.state, "session_manager", None)
    repo = getattr(request.app.state, "repository", None)
    db = getattr(request.app.state, "db", None)

    target_account_id = x_gemini_account or req.account_id
    if not target_account_id and session_mgr:
        target_account_id = session_mgr.active_account_id

    if session_mgr and target_account_id and get_gemini_client not in request.app.dependency_overrides:
        acc_health = session_mgr.get_health(target_account_id)
        if acc_health.status == SessionHealthStatus.UNAUTHENTICATED:
            return make_anthropic_error(
                "Account session is not authenticated",
                error_type="authentication_error",
                status_code=401,
            )
        ctx = session_mgr.get_session_context(target_account_id)
        if ctx:
            client = GeminiWebClient(session_context=ctx, base_url=session_mgr.base_url)

    if not client:
        return make_anthropic_error(
            "Gemini bridge client not available or unauthenticated",
            error_type="authentication_error",
            status_code=401,
        )

    # 4. Conversation Prefix Cache Lookup & Turn Extraction
    has_tools = bool(req.tools and (not req.tool_choice or (isinstance(req.tool_choice, str) and req.tool_choice != "none") or (isinstance(req.tool_choice, dict) and req.tool_choice.get("type") != "none")))
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
            delta_sys = AnthropicAdapter.extract_system_instruction(None, tools=req.tools, tool_choice=req.tool_choice) if has_tools else None
            prompt, delta_history, image_descriptors = await AnthropicAdapter.extract_turns_and_attachments(
                delta_messages, system_instruction=delta_sys
            )
            if delta_history:
                hist_lines = [f"[{h['role']}]: {h['content']}" for h in delta_history if h.get("content")]
                if hist_lines:
                    prompt = "\n\n".join(hist_lines + ([prompt] if prompt else []))
            history = None
        else:
            system_inst = AnthropicAdapter.extract_system_instruction(
                req.system,
                tools=req.tools,
                tool_choice=req.tool_choice,
            )
            prompt, history, image_descriptors = await AnthropicAdapter.extract_turns_and_attachments(
                req.messages, system_instruction=system_inst
            )
    except InvalidImagePayloadError as e:
        return make_anthropic_error(str(e), error_type="invalid_request_error", status_code=400)
    except Exception as e:
        logger.error("Error extracting Anthropic turns: %s", e, exc_info=True)
        return make_anthropic_error(f"Failed parsing message payload: {e}", error_type="invalid_request_error", status_code=400)

    # 5. Upload Attachments if any
    attachments: List[Dict[str, str]] = []
    if image_descriptors and uploader:
        try:
            for desc in image_descriptors:
                uri = await uploader.upload_file(data=desc.data, filename=desc.filename, mime_type=desc.mime_type)
                attachments.append({"uri": uri, "filename": desc.filename})
        except Exception as e:
            logger.error("Attachment upload failure: %s", e)
            return make_anthropic_error(f"Failed uploading image attachment: {e}", error_type="api_error", status_code=500)

    # 6. Parse Stop Sequences
    stop_seqs: Optional[List[str]] = req.stop_sequences

    message_id = generate_message_id()
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
        if subagent_feature_mode is not None:
            kw["feature_mode"] = subagent_feature_mode
        return kw

    def _remember_turn(reply_content: Any) -> None:
        cid_val = done_metadata.get("cid") or resume_cid
        if not cid_val:
            return
        chat_state = {
            "cid": str(cid_val),
            "rid": str(done_metadata.get("rid") or resume_rid or ""),
            "rcid": str(done_metadata.get("rcid") or resume_rcid or ""),
        }
        conversation_cache.remember(req.messages, reply_content, chat_state)

    async def create_stream_with_failover() -> AsyncIterator[StreamChunk]:
        nonlocal client, target_account_id, failover_occurred, failover_from, fulfilled_by_id, done_metadata
        current_acc_id = target_account_id
        required_tier = "pro" if (canonical_model in ("95a4c0abcba24364", "e6fa609c3fa255c0") or "pro" in req.model.lower()) else "free"

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
            is_quota = "1037" in err_str or "429" in err_str
            if is_quota and session_mgr:
                session_mgr.mark_cooldown(current_acc_id, model_id=canonical_model, duration_seconds=3600.0, reason=err_str)
                backup = session_mgr.get_failover_account(canonical_model, current_acc_id, required_tier=required_tier)
                if backup:
                    logger.info("Anthropic gateway failing over from %s to %s due to %s", current_acc_id, backup.account_id, err)
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

    input_tokens = AnthropicAdapter.count_tokens(req)
    thinking_enabled = not (req.thinking and req.thinking.get("type") == "disabled")

    # 7. Execution: Streaming vs Non-Streaming
    try:
        if req.stream:
            accumulated_chunks: List[str] = []
            accumulated_thoughts: List[str] = []

            async def tracking_stream_generator():
                nonlocal accumulated_chunks, accumulated_thoughts
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
                        elif chunk.type == "thought" and chunk.delta:
                            accumulated_thoughts.append(chunk.delta)
                        yield chunk
                except Exception as stream_err:
                    logger.error("Streaming error in Anthropic handler: %s", stream_err)
                    raise

            async def tracked_sse_response():
                try:
                    async for line in stream_anthropic_sse(
                        stream_generator=tracking_stream_generator(),
                        request_id=message_id,
                        model_name=req.model,
                        input_tokens=input_tokens,
                        stop_sequences=stop_seqs,
                        has_tools=has_tools,
                        thinking_enabled=thinking_enabled,
                    ):
                        yield line

                    final_streamed_text = "".join(accumulated_chunks)
                    if has_tools:
                        clean_text, tool_calls = AnthropicAdapter.parse_tool_calls(final_streamed_text)
                        response_blocks = []
                        if clean_text:
                            response_blocks.append({"type": "text", "text": clean_text})
                        if tool_calls:
                            for tc in tool_calls:
                                response_blocks.append(tc.model_dump())
                        if not response_blocks and final_streamed_text:
                            response_blocks.append({"type": "text", "text": final_streamed_text})
                        _remember_turn({"role": "assistant", "content": response_blocks})
                    else:
                        _remember_turn(final_streamed_text)
                finally:
                    if client and hasattr(client, "close"):
                        try:
                            await client.close()
                        except Exception:
                            pass

            sse_headers = {
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
                "X-Gemini-Model-Resolved": canonical_model,
            }
            if subagent_profile:
                sse_headers["X-Gemini-Subagent"] = str(subagent_profile["name"])
            if gem_id:
                sse_headers["X-Gemini-Gem-Id"] = str(gem_id)

            return StreamingResponse(
                tracked_sse_response(),
                media_type="text/event-stream",
                headers=sse_headers,
            )
        else:
            # Non-streaming execution
            full_text_chunks: List[str] = []
            thought_chunks: List[str] = []

            try:
                gen = create_stream_with_failover()
                async for chunk in gen:
                    if chunk.type == "text" and chunk.delta:
                        full_text_chunks.append(chunk.delta)
                    elif chunk.type == "thought" and chunk.delta:
                        thought_chunks.append(chunk.delta)
            except Exception as e:
                err_str = str(e)
                if "1037" in err_str or "429" in err_str:
                    return make_anthropic_error("Upstream rate limit exceeded", error_type="rate_limit_error", status_code=429)
                return make_anthropic_error(f"Generation failed: {e}", error_type="api_error", status_code=502)
            finally:
                if client and hasattr(client, "close"):
                    try:
                        await client.close()
                    except Exception:
                        pass

            raw_reply_text = "".join(full_text_chunks)
            thought_text = "".join(thought_chunks)

            # Check stop sequences
            stop_reason = "end_turn"
            stop_matched = None
            if stop_seqs:
                for stop_seq in stop_seqs:
                    if stop_seq in raw_reply_text:
                        idx = raw_reply_text.find(stop_seq)
                        raw_reply_text = raw_reply_text[:idx]
                        stop_reason = "stop_sequence"
                        stop_matched = stop_seq
                        break

            content_blocks: List[ContentBlock] = []

            # Add thinking block if thoughts exist
            if thought_text and thinking_enabled:
                content_blocks.append(ThinkingBlock(thinking=thought_text))

            # Parse tool calls if tools were provided
            if has_tools:
                clean_text, tool_calls = AnthropicAdapter.parse_tool_calls(raw_reply_text)
                if clean_text:
                    content_blocks.append(TextBlock(text=clean_text))
                if tool_calls:
                    for tc in tool_calls:
                        content_blocks.append(tc)
                    if stop_reason == "end_turn":
                        stop_reason = "tool_use"
                elif not clean_text and raw_reply_text:
                    content_blocks.append(TextBlock(text=raw_reply_text))
            else:
                content_blocks.append(TextBlock(text=raw_reply_text))

            output_tokens = max(1, (len(raw_reply_text) + len(thought_text)) // 3)

            # Store in conversation cache
            stored_content = [b.model_dump() if hasattr(b, "model_dump") else b for b in content_blocks]
            _remember_turn({"role": "assistant", "content": stored_content})

            response.headers["X-Gemini-Model-Resolved"] = canonical_model
            if subagent_profile:
                response.headers["X-Gemini-Subagent"] = str(subagent_profile["name"])
            if gem_id:
                response.headers["X-Gemini-Gem-Id"] = str(gem_id)

            return MessageResponse(
                id=message_id,
                model=req.model,
                content=content_blocks,
                stop_reason=stop_reason,
                stop_sequence=stop_matched,
                usage=MessageUsage(
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                ),
            )

    except Exception as e:
        logger.error("Unhandled error in Anthropic create_message: %s", e, exc_info=True)
        return make_anthropic_error(f"Internal server error: {e}", error_type="api_error", status_code=500)


@router.post("/messages/count_tokens", response_model=CountTokensResponse)
@router.post("/v1/messages/count_tokens", response_model=CountTokensResponse, include_in_schema=False)
async def count_anthropic_tokens(req: CountTokensRequest) -> CountTokensResponse:
    """Calculates estimated token consumption for an Anthropic message payload."""
    tokens = AnthropicAdapter.count_tokens(req)
    return CountTokensResponse(input_tokens=tokens)
