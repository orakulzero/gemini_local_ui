"""
backend/app/api/ws_chat.py
Loopback JSON-RPC 2.0 WebSocket endpoint (`/api/chat/ws`) implementing `agy.stream.v1`.
"""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import json
import logging
import os
from typing import Any
import uuid

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect

from backend.app.api import chat as chat_mod
from backend.app.contracts.agent_stream_v1 import (
    AGENT_PROTOCOL_VERSION,
    build_session_update_frame,
)
from backend.app.mcp.approval_gate import get_approval_gate
from backend.app.mcp.elicitation_gate import get_elicitation_gate
from backend.app.security.origin_guard import validate_host_and_origin
from backend.app.services.chat_orchestrator import prepare_chat_turn, stream_chat_turn
from backend.app.services.stream_events import KeepaliveEvent

logger = logging.getLogger("gemini_api.ws_chat")
router = APIRouter(prefix="/api/chat", tags=["Web UI Chat WS"])


def _verify_ws_security(websocket: WebSocket) -> bool:
    """Validate Host, Origin, and optional GEMINI_LOCAL_API_TOKEN before accept."""
    try:
        validate_host_and_origin(
            host=websocket.headers.get("host"),
            origin=websocket.headers.get("origin"),
        )
    except HTTPException:
        return False

    expected_token = os.environ.get("GEMINI_LOCAL_API_TOKEN", "").strip()
    if expected_token:
        auth_hdr = websocket.headers.get("authorization", "").strip()
        provided = ""
        if auth_hdr.lower().startswith("bearer "):
            provided = auth_hdr[7:].strip()
        elif "x-local-api-token" in websocket.headers:
            provided = websocket.headers["x-local-api-token"].strip()
        elif "token" in websocket.query_params:
            provided = websocket.query_params["token"].strip()
        if not provided or not hmac.compare_digest(provided, expected_token):
            return False
    return True


@router.websocket("/ws")
async def chat_ws_endpoint(websocket: WebSocket) -> None:
    if not _verify_ws_security(websocket):
        await websocket.close(code=1008)
        return

    await websocket.accept()
    send_lock = asyncio.Lock()
    active_cancel: asyncio.Event | None = None
    active_task: asyncio.Task[None] | None = None

    async def _send(frame: dict[str, Any]) -> None:
        async with send_lock:
            await websocket.send_json(frame)

    async def _run_prompt(req_id: Any, params: dict[str, Any], cancel_ev: asyncio.Event) -> None:
        trace_id = str(params.get("trace_id") or uuid.uuid4().hex)
        http_scope = {**websocket.scope, "type": "http", "method": "POST"}
        req_shim = Request(http_scope)
        req_shim.state.trace_id = trace_id
        payload = params.get("request") if isinstance(params.get("request"), dict) else params
        try:
            chat_req = chat_mod.ChatGenerateRequest(**payload)
            override_fn = websocket.app.dependency_overrides.get(chat_mod.get_gemini_client)
            cli = override_fn(req_shim) if override_fn else chat_mod.get_gemini_client(req_shim)
            repo = chat_mod.get_repository(req_shim)
            prepared = await prepare_chat_turn(chat_req, req_shim, client=cli, repo=repo)
            async for ev in stream_chat_turn(prepared, cancel_event=cancel_ev):
                if isinstance(ev, KeepaliveEvent):
                    continue
                await _send(build_session_update_frame(ev.to_dict(), trace_id=prepared.trace_id))
            if req_id is not None:
                await _send({
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "ok": True,
                        "protocol_version": AGENT_PROTOCOL_VERSION,
                        "conversation_id": prepared.conv_id,
                        "trace_id": prepared.trace_id,
                    },
                })
        except HTTPException as http_exc:
            await _send({
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32602, "message": str(http_exc.detail)},
            })
        except Exception as exc:
            logger.error("WS session/prompt failed: %s", exc)
            await _send({
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32603, "message": str(exc)},
            })

    try:
        while True:
            raw_text = await websocket.receive_text()
            try:
                msg = json.loads(raw_text)
            except Exception:
                await _send({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}})
                continue
            if not isinstance(msg, dict):
                await _send({"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid Request"}})
                continue

            req_id = msg.get("id")
            method = msg.get("method")
            params = msg.get("params") if isinstance(msg.get("params"), dict) else {}

            if method == "ping":
                await _send({"jsonrpc": "2.0", "id": req_id, "result": {"pong": True, "protocol_version": AGENT_PROTOCOL_VERSION}})
            elif method == "session/cancel":
                if active_cancel is not None:
                    active_cancel.set()
                if req_id is not None:
                    await _send({"jsonrpc": "2.0", "id": req_id, "result": {"ok": True, "cancelled": True}})
            elif method == "session/respond_permission":
                ok = get_approval_gate().submit_decision(str(params.get("approval_id", "")), str(params.get("decision", "deny")))  # type: ignore[arg-type]
                if req_id is not None:
                    await _send({"jsonrpc": "2.0", "id": req_id, "result": {"ok": ok}})
            elif method == "elicitation/respond":
                ok = get_elicitation_gate().submit_response(
                    str(params.get("elicitation_id", "")),
                    str(params.get("action", "cancel")),  # type: ignore[arg-type]
                    params.get("content") if isinstance(params.get("content"), dict) else None,
                )
                if req_id is not None:
                    await _send({"jsonrpc": "2.0", "id": req_id, "result": {"ok": ok}})
            elif method == "session/prompt":
                if active_cancel is not None:
                    active_cancel.set()
                if active_task is not None and not active_task.done():
                    active_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        await active_task
                active_cancel = asyncio.Event()
                active_task = asyncio.create_task(_run_prompt(req_id, params, active_cancel))
            else:
                await _send({"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": f"Method not found: {method}"}})
    except WebSocketDisconnect:
        pass
    finally:
        if active_cancel is not None:
            active_cancel.set()
        if active_task is not None and not active_task.done():
            active_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await active_task
