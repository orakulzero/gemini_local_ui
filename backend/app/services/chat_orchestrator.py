"""
backend/app/services/chat_orchestrator.py
Turn preparation, multi-account quota failover, 2-round MCP tool loop,
rich media post-processing, and SQLite persistence for Web UI chat streaming.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
import contextlib
from dataclasses import dataclass
import logging
import time
from typing import Any
import uuid

from fastapi import HTTPException, Request

from backend.app.bridge.client import GeminiWebClient
from backend.app.bridge.exceptions import (
    AuthenticationError,
    GeminiBridgeError,
    ModelInconsistentError,
)
from backend.app.bridge.models import DEFAULT_MODELS
from backend.app.mcp.tool_fence_filter import ToolFenceFilter
from backend.app.observability.telemetry import (
    _build_parent_span_context,
    bind_trace_context,
    current_trace_id,
    get_tracer,
)
from backend.app.services.media_postprocessor import (
    MediaAccumulator,
    localize_generated_images,
)
from backend.app.services.prompt_assembler import (
    AssembledPrompt,
    assemble_prompt,
    resolve_prior_context_ids,
)
from backend.app.services.stream_events import (
    ChatStreamEvent,
    CitationEvent,
    DoneEvent,
    ErrorEvent,
    FailoverEvent,
    GroundingEvent,
    MediaEvent,
    SavedEvent,
    StartEvent,
    TextEvent,
    ThoughtEvent,
)
from backend.app.services.tool_runtime import (
    ToolExecutionOutcome,
    execute_tool_with_hitl,
    extract_tool_call_block,
    strip_tool_call_fences,
)

logger = logging.getLogger("gemini_backend.chat_orchestrator")


def estimate_token_count(text: str) -> int:
    """Estimate tokens using calibrated multi-lingual sentencepiece heuristics."""
    if not text:
        return 0
    cyrillic_chars = sum(1 for c in text if "\u0400" <= c <= "\u04FF")
    if cyrillic_chars > len(text) * 0.3:
        return max(1, int(len(text) / 1.8))
    return max(1, int(len(text) / 3.8))


@dataclass
class PreparedTurn:
    req: Any
    conv_id: str
    user_msg_id: int | None
    target_account_id: str
    is_incognito: bool
    assembled: AssembledPrompt
    client: Any
    repo: Any
    db: Any
    session_mgr: Any
    image_data_dir: Any = None
    trace_id: str = ""


async def prepare_chat_turn(
    req: Any,
    request: Request,
    *,
    client: Any,
    repo: Any = None,
) -> PreparedTurn:
    """Validate request, assemble prompt/tools, and persist user turn before streaming."""
    session_mgr = getattr(request.app.state, "session_manager", None)
    if not session_mgr:
        import backend.app.main as main_mod
        session_mgr = getattr(main_mod, "session_manager", None)

    db = getattr(request.app.state, "db", None)
    if not db:
        import backend.app.main as main_mod
        db = getattr(main_mod, "db", None)

    trace_id = getattr(request.state, "trace_id", "") or current_trace_id()
    raw_acc = request.headers.get("x-gemini-account")
    if raw_acc in ("None", "null", ""):
        raw_acc = None
    req_acc = req.account_id if req.account_id not in ("None", "null", "") else None
    target_account_id = raw_acc or req_acc or (session_mgr.active_account_id if session_mgr else "default")
    if target_account_id in ("None", "null", ""):
        target_account_id = session_mgr.active_account_id if session_mgr else "default"

    if session_mgr and isinstance(client, GeminiWebClient):
        ctx = session_mgr.get_session_context(target_account_id)
        client = GeminiWebClient(
            session_context=ctx,
            base_url=session_mgr.base_url,
            vault=session_mgr.vault,
            account_id=target_account_id,
        )

    existing_msg = None
    if req.existing_user_message_id is not None and repo:
        existing_msg = await repo.get_message(int(req.existing_user_message_id))

    assembled = await assemble_prompt(
        req,
        existing_msg=existing_msg,
        rag_service=getattr(request.app.state, "rag_service", None),
        base_url=session_mgr.base_url if session_mgr else None,
    )

    if not assembled.clean_user_prompt.strip() and not req.attachments and not assembled.bridge_attachments:
        raise HTTPException(status_code=400, detail="Prompt or attachments must be provided")

    is_incognito = bool(req.incognito)
    conv_id = req.conversation_id or f"conv_{uuid.uuid4().hex[:12]}"
    user_msg_id: int | None = req.existing_user_message_id
    now_ms = int(time.time() * 1000)

    if not is_incognito and repo and user_msg_id is None:
        try:
            existing_conv = await repo.get_conversation(conv_id)
            if not existing_conv:
                await repo.create_conversation(
                    title=assembled.clean_user_prompt[:50] if assembled.clean_user_prompt else "New Conversation",
                    model=req.model,
                    gem_id=req.gem_id,
                    conversation_id=conv_id,
                )
            parent_id = (
                int(req.parent_message_id)
                if req.parent_message_id is not None and str(req.parent_message_id).isdigit()
                else None
            )
            user_msg = await repo.add_message(
                conversation_id=conv_id,
                role="user",
                content=assembled.clean_storage_text,
                parent_id=parent_id,
                model=req.model,
                created_at=now_ms,
            )
            user_msg_id = user_msg.id
            for att in req.attachments or []:
                await repo.add_attachment(
                    message_id=user_msg.id,
                    filename=att.filename,
                    mime_type=att.mime_type,
                    size_bytes=att.file_size,
                    storage_path=att.storage_path or "",
                    uri=att.uri or "",
                )
        except Exception as err:
            logger.warning("Failed persisting user message in repository: %s", err)
    elif is_incognito and user_msg_id is None:
        user_msg_id = now_ms

    return PreparedTurn(
        req=req,
        conv_id=conv_id,
        user_msg_id=user_msg_id,
        target_account_id=target_account_id,
        is_incognito=is_incognito,
        assembled=assembled,
        client=client,
        repo=repo,
        db=db,
        session_mgr=session_mgr,
        image_data_dir=getattr(request.app.state, "image_data_dir", None),
        trace_id=trace_id,
    )


def _media_dict_to_event(evt_dict: dict[str, Any]) -> ChatStreamEvent:
    etype = evt_dict["type"]
    meta = evt_dict.get("metadata", {})
    if etype == "citation":
        return CitationEvent(metadata=meta)
    if etype == "grounding":
        return GroundingEvent(metadata=meta)
    return MediaEvent(metadata=meta)


async def _persist_stream_error(
    *,
    prepared: PreparedTurn,
    err: Exception,
    full_thought_list: list[str],
    start_time: float,
    fulfilled_by_id: str,
    is_task_or_research: bool,
    failover_occurred: bool,
    failover_from: str | None,
    include_typed_prefix: bool = True,
) -> ErrorEvent:
    req = prepared.req
    if not prepared.is_incognito and prepared.repo and prepared.user_msg_id is not None:
        with contextlib.suppress(Exception):
            await prepared.repo.add_message(
                conversation_id=prepared.conv_id,
                role="assistant",
                content=f"Error generating response: {err}",
                parent_id=prepared.user_msg_id,
                thoughts="".join(full_thought_list) if full_thought_list else None,
                model=req.model,
                is_error=True,
                created_at=int(time.time() * 1000),
            )
    if prepared.db:
        with contextlib.suppress(Exception):
            dur_ms = int((time.time() - start_time) * 1000)
            s_err = str(err)
            err_code_val = 1037 if "1037" in s_err else (429 if "429" in s_err else (1061 if "1061" in s_err else 500))
            await prepared.db.record_telemetry(
                account_id=fulfilled_by_id,
                model_id=req.model,
                model_name=req.model,
                feature_mode="task" if is_task_or_research else "chat",
                status_code=err_code_val,
                error_code=s_err,
                duration_ms=dur_ms,
                was_failover=1 if failover_occurred else 0,
                failover_from_account=failover_from,
            )
    msg = f"{type(err).__name__}: {err}" if include_typed_prefix else str(err)
    return ErrorEvent(error=msg)


def _sync_done_media(done_meta: dict[str, Any], media_acc: MediaAccumulator) -> None:
    if "citations" in done_meta and not media_acc.citations:
        media_acc.citations = done_meta["citations"]
    if "images" in done_meta and not media_acc.images:
        media_acc.images = done_meta["images"]
    if "youtube" in done_meta and not media_acc.youtube:
        media_acc.youtube = done_meta["youtube"]
    if "code_executions" in done_meta:
        media_acc.code_executions = done_meta["code_executions"]
    if "grounding" in done_meta and not media_acc.grounding:
        media_acc.grounding = done_meta["grounding"]


async def stream_chat_turn(
    prepared: PreparedTurn,
    *,
    cancel_event: asyncio.Event,
) -> AsyncIterator[ChatStreamEvent]:
    """Execute full chat generation turn wrapped in OTel span `chat.orchestrator.turn`."""
    req = prepared.req
    session_mgr = prepared.session_mgr
    repo = prepared.repo
    db = prepared.db
    conv_id = prepared.conv_id
    user_msg_id = prepared.user_msg_id
    is_incognito = prepared.is_incognito
    effective_prompt = prepared.assembled.wire_prompt
    active_tools = prepared.assembled.active_tools
    bridge_attachments = prepared.assembled.bridge_attachments

    if prepared.trace_id:
        bind_trace_context(prepared.trace_id)

    tracer = get_tracer("gemini_local_ui.orchestrator")
    parent_ctx = _build_parent_span_context(prepared.trace_id) if prepared.trace_id else None
    span_cm = (
        tracer.start_as_current_span("chat.orchestrator.turn", context=parent_ctx)
        if parent_ctx is not None
        else tracer.start_as_current_span("chat.orchestrator.turn")
    )

    client_active: Any = None
    backup_cli: Any = None
    with span_cm as turn_span:
        turn_span.set_attribute("chat.conversation_id", conv_id)
        turn_span.set_attribute("chat.model", req.model)
        if prepared.trace_id:
            turn_span.set_attribute("trace_id", prepared.trace_id)

        yield StartEvent(
            conversation_id=conv_id,
            user_message_id=user_msg_id if user_msg_id is not None else 0,
            trace_id=prepared.trace_id,
        )

        start_time = time.time()
        full_text_list: list[str] = []
        full_thought_list: list[str] = []
        text_emitted = False

        current_acc_id = prepared.target_account_id
        original_account_id = prepared.target_account_id
        failover_occurred = False
        failover_from: str | None = None

        orig_sess = session_mgr.get_account_session(current_acc_id) if session_mgr else None
        fulfilled_by_id = current_acc_id
        fulfilled_by_name = orig_sess.profile.name if orig_sess and orig_sess.profile else current_acc_id

        is_task_or_research = (req.feature_mode or "").lower() in (
            "task", "tasks", "40", "spark", "deep-research", "deep_research", "deepresearch", "research",
        )
        is_high_tier_mode = (
            req.model in ("95a4c0abcba24364", "e6fa609c3fa255c0", "gemini-4-carbon")
            or "pro" in req.model.lower()
            or "argon" in req.model.lower()
            or "carbon" in req.model.lower()
            or is_task_or_research
        )
        required_tier = "pro" if is_high_tier_mode else "free"
        if session_mgr and session_mgr.is_in_cooldown(current_acc_id, model_id=req.model):
            backup = session_mgr.get_failover_account(req.model, current_acc_id, required_tier=required_tier)
            if backup:
                failover_occurred = True
                failover_from = current_acc_id
                fulfilled_by_id = backup.account_id
                fulfilled_by_name = backup.profile.name
                yield FailoverEvent({
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
                client_active = prepared.client
        else:
            client_active = prepared.client

        effective_model = req.model
        if is_task_or_research and "@mode=" not in effective_model:
            mode_suffix = "deep-research" if "research" in (req.feature_mode or "").lower() else "task"
            effective_model = f"{effective_model}@mode={mode_suffix}"

        eff_cid: str | None = req.cid
        eff_rid: str | None = req.rid
        eff_rcid: str | None = req.rcid
        if not is_incognito and repo and not eff_cid and req.existing_user_message_id is None:
            eff_cid, eff_rid, eff_rcid = await resolve_prior_context_ids(
                repo, conv_id, req.parent_message_id, current_acc_id
            )

        recorded_tool_calls: list[dict[str, Any]] = []
        media_acc = MediaAccumulator()
        done_cid: str | None = eff_cid
        done_rid: str | None = eff_rid
        done_rcid: str | None = eff_rcid

        async def run_stream(
            active_cli: Any,
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

        try:
            try:
                current_stream_prompt = effective_prompt
                tool_rounds = 0
                max_tool_rounds = 2 if active_tools else 1

                while tool_rounds < max_tool_rounds:
                    if cancel_event.is_set():
                        return
                    tool_rounds += 1
                    has_tool_call = False
                    round_full_text: list[str] = []
                    raw_round_text: list[str] = []
                    fence_filter = ToolFenceFilter() if active_tools else None

                    async for chunk in run_stream(
                        client_active, eff_cid, eff_rid, eff_rcid, prompt_override=current_stream_prompt
                    ):
                        if cancel_event.is_set():
                            return
                        if chunk.type == "thought":
                            full_thought_list.append(chunk.delta)
                            yield ThoughtEvent(delta=chunk.delta)
                        elif chunk.type == "text":
                            raw_round_text.append(chunk.delta)
                            filtered_delta = fence_filter.feed(chunk.delta) if fence_filter else chunk.delta
                            if filtered_delta:
                                text_emitted = True
                                round_full_text.append(filtered_delta)
                                yield TextEvent(delta=filtered_delta)
                        elif chunk.type in ("citation", "grounding", "media") and chunk.metadata:
                            evt_dict = media_acc.handle_chunk(chunk.type, chunk.metadata)
                            if evt_dict:
                                yield _media_dict_to_event(evt_dict)
                        elif chunk.type == "done":
                            if fence_filter:
                                flushed = fence_filter.flush()
                                if flushed:
                                    text_emitted = True
                                    round_full_text.append(flushed)
                                    yield TextEvent(delta=flushed)
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
                            _sync_done_media(done_meta, media_acc)

                            candidate_text = done_meta.get("full_text") or "".join(raw_round_text)
                            parsed_tool = extract_tool_call_block(candidate_text) if active_tools else None
                            if parsed_tool and tool_rounds < max_tool_rounds:
                                t_name, t_args = parsed_tool
                                outcome = ToolExecutionOutcome()
                                try:
                                    async for tool_ev in execute_tool_with_hitl(
                                        tool_name=t_name,
                                        tool_args=t_args,
                                        active_tools=active_tools,
                                        effective_prompt=effective_prompt,
                                        cancel_event=cancel_event,
                                        outcome=outcome,
                                    ):
                                        yield tool_ev
                                    if outcome.cancelled or cancel_event.is_set():
                                        return
                                    if outcome.record:
                                        recorded_tool_calls.append(outcome.record)
                                    if outcome.followup_prompt:
                                        has_tool_call = True
                                        current_stream_prompt = outcome.followup_prompt
                                        break
                                except Exception as t_err:
                                    logger.warning("Tool call execution error: %s", t_err)
                            if not round_full_text and done_meta.get("full_text"):
                                recovered_text = strip_tool_call_fences(done_meta["full_text"])
                                if recovered_text:
                                    round_full_text.append(recovered_text)
                                    text_emitted = True
                                    yield TextEvent(delta=recovered_text)
                            full_text_list = round_full_text

                            if media_acc.images:
                                localized = await localize_generated_images(
                                    media_acc.images,
                                    client_active,
                                    done_cid,
                                    done_rid,
                                    done_rcid,
                                    data_dir=prepared.image_data_dir,
                                )
                                if localized:
                                    yield MediaEvent(metadata={
                                        "images": media_acc.images,
                                        "youtube": media_acc.youtube,
                                        **({"code_executions": media_acc.code_executions} if media_acc.code_executions else {}),
                                    })
                            done_meta["images"] = media_acc.images
                            if media_acc.code_executions:
                                done_meta["code_executions"] = media_acc.code_executions
                            if recorded_tool_calls:
                                done_meta["tool_calls"] = recorded_tool_calls
                            if prepared.assembled.rag_sources:
                                done_meta["rag_sources"] = prepared.assembled.rag_sources
                            if "full_text" in done_meta:
                                done_meta["full_text"] = strip_tool_call_fences(done_meta["full_text"])
                            yield DoneEvent(metadata=done_meta)

                    if not has_tool_call:
                        break
            except Exception as stream_err:
                err_code = getattr(stream_err, "code", None)
                err_str = str(stream_err)
                is_quota = (
                    err_code in (1037, 429, 1061)
                    or any(tok in err_str for tok in ("1037", "429", "1061"))
                    or "quota" in err_str.lower()
                    or "rate limit" in err_str.lower()
                )
                is_recoverable = isinstance(
                    stream_err, (GeminiBridgeError, AuthenticationError, ModelInconsistentError)
                )
                if (is_quota or is_recoverable) and session_mgr and not text_emitted:
                    if is_quota:
                        session_mgr.mark_cooldown(current_acc_id, model_id=req.model, duration_seconds=3600.0, reason=err_str)
                    elif isinstance(stream_err, AuthenticationError):
                        session_mgr.mark_cooldown(current_acc_id, model_id=None, duration_seconds=300.0, reason=err_str)
                    backup = session_mgr.get_failover_account(req.model, current_acc_id, required_tier=required_tier)
                    if backup:
                        if not is_quota and not isinstance(stream_err, AuthenticationError):
                            session_mgr.mark_cooldown(current_acc_id, model_id=req.model, duration_seconds=120.0, reason=err_str)
                        failover_occurred = True
                        failover_from = current_acc_id
                        fulfilled_by_id = backup.account_id
                        fulfilled_by_name = backup.profile.name
                        if is_quota:
                            code = 1061 if (err_code == 1061 or "1061" in err_str) else (429 if (err_code == 429 or "429" in err_str) else 1037)
                        else:
                            code = err_code if isinstance(err_code, int) else (7 if isinstance(stream_err, AuthenticationError) else 500)
                        yield FailoverEvent({
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
                        done_cid = done_rid = done_rcid = None
                        async for chunk in run_stream(backup_cli, None, None, None):
                            if cancel_event.is_set():
                                return
                            if chunk.type == "thought":
                                full_thought_list.append(chunk.delta)
                                yield ThoughtEvent(delta=chunk.delta)
                            elif chunk.type == "text":
                                full_text_list.append(chunk.delta)
                                yield TextEvent(delta=chunk.delta)
                            elif chunk.type in ("citation", "grounding", "media") and chunk.metadata:
                                evt_dict = media_acc.handle_chunk(chunk.type, chunk.metadata)
                                if evt_dict:
                                    yield _media_dict_to_event(evt_dict)
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
                                _sync_done_media(done_meta, media_acc)
                                if not full_text_list and done_meta.get("full_text"):
                                    recovered_text = done_meta["full_text"]
                                    full_text_list.append(recovered_text)
                                    text_emitted = True
                                    yield TextEvent(delta=recovered_text)
                                if media_acc.images:
                                    localized = await localize_generated_images(
                                        media_acc.images,
                                        backup_cli,
                                        done_cid,
                                        done_rid,
                                        done_rcid,
                                        data_dir=prepared.image_data_dir,
                                    )
                                    if localized:
                                        yield MediaEvent(metadata={
                                            "images": media_acc.images,
                                            "youtube": media_acc.youtube,
                                            **({"code_executions": media_acc.code_executions} if media_acc.code_executions else {}),
                                        })
                                done_meta["images"] = media_acc.images
                                if media_acc.code_executions:
                                    done_meta["code_executions"] = media_acc.code_executions
                                yield DoneEvent(metadata=done_meta)
                    else:
                        logger.error("Non-quota stream error: %s", stream_err, exc_info=True)
                        yield await _persist_stream_error(
                            prepared=prepared,
                            err=stream_err,
                            full_thought_list=full_thought_list,
                            start_time=start_time,
                            fulfilled_by_id=fulfilled_by_id,
                            is_task_or_research=is_task_or_research,
                            failover_occurred=failover_occurred,
                            failover_from=failover_from,
                        )
                        return
                else:
                    logger.error("Stream error occurred: %s", stream_err, exc_info=True)
                    yield await _persist_stream_error(
                        prepared=prepared,
                        err=stream_err,
                        full_thought_list=full_thought_list,
                        start_time=start_time,
                        fulfilled_by_id=fulfilled_by_id,
                        is_task_or_research=is_task_or_research,
                        failover_occurred=failover_occurred,
                        failover_from=failover_from,
                    )
                    return

            full_text = strip_tool_call_fences("".join(full_text_list))
            full_thoughts = "".join(full_thought_list) if full_thought_list else None

            asst_msg_id = None
            if not is_incognito and repo and user_msg_id is not None:
                try:
                    rich_meta = media_acc.build_rich_metadata(
                        fulfilled_by_id=fulfilled_by_id,
                        done_cid=done_cid,
                        done_rid=done_rid,
                        done_rcid=done_rcid,
                        tool_calls=recorded_tool_calls or None,
                    )
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
                except Exception as err:
                    logger.error("Failed persisting assistant response in repository: %s", err)

            yield SavedEvent(assistant_message_id=asst_msg_id)  # type: ignore[arg-type]

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

        except Exception as outer_err:
            logger.error("Streaming error in stream_chat_turn: %s", outer_err)
            yield await _persist_stream_error(
                prepared=prepared,
                err=outer_err,
                full_thought_list=full_thought_list,
                start_time=start_time,
                fulfilled_by_id=fulfilled_by_id,
                is_task_or_research=is_task_or_research,
                failover_occurred=failover_occurred,
                failover_from=failover_from,
                include_typed_prefix=False,
            )
        finally:
            for cli in (client_active, prepared.client, backup_cli):
                if cli and hasattr(cli, "close"):
                    with contextlib.suppress(Exception):
                        await cli.close()
