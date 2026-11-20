"""
backend/app/services/tool_runtime.py
MCP tool block parsing, human-in-the-loop (HITL) approval gating, and execution runtime.
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
import json
import logging
import re
import time
from typing import Any, AsyncIterator
import uuid

from backend.app.observability.telemetry import get_tracer
from backend.app.services.stream_events import (
    ChatStreamEvent,
    KeepaliveEvent,
    ToolApprovalRequiredEvent,
    ToolCallEvent,
)

logger = logging.getLogger("gemini_backend.tool_runtime")


@dataclass
class ToolExecutionOutcome:
    cancelled: bool = False
    denied: bool = False
    record: dict[str, Any] | None = None
    followup_prompt: str | None = None


def extract_tool_call_block(candidate_text: str) -> tuple[str, dict[str, Any]] | None:
    """Extract (tool_name, arguments) from ```tool_call ... ``` fence block."""
    if not candidate_text:
        return None
    m = re.search(r"```tool_call\s*\n(.*?)\n```", candidate_text, re.DOTALL)
    if not m:
        return None
    try:
        data = json.loads(m.group(1).strip())
        name = data.get("name")
        args = data.get("arguments", {})
        if isinstance(name, str) and name:
            return name, args if isinstance(args, dict) else {}
    except Exception as err:
        logger.debug("Failed parsing tool_call json: %s", err)
    return None


def strip_tool_call_fences(text: str) -> str:
    """Strip ```tool_call ... ``` block from model text for UI rendering."""
    if not text:
        return ""
    return re.sub(r"```tool_call\s*\n.*?\n```", "", text, flags=re.DOTALL).strip()


async def execute_tool_with_hitl(
    *,
    tool_name: str,
    tool_args: dict[str, Any],
    active_tools: list[dict[str, Any]],
    effective_prompt: str,
    cancel_event: asyncio.Event,
    outcome: ToolExecutionOutcome,
    workspace_store: Any = None,
    mcp_manager: Any = None,
    approval_gate: Any = None,
    approval_timeout: float = 120.0,
    keepalive_interval: float = 15.0,
) -> AsyncIterator[ChatStreamEvent]:
    """Evaluate HITL policy, request user approval if required, and execute tool under OTel span."""
    if approval_gate is None:
        from backend.app.mcp.approval_gate import get_approval_gate
        gate = get_approval_gate()
    else:
        gate = approval_gate

    if workspace_store is None:
        from backend.app.api.workspace import get_workspace_store
        store = get_workspace_store()
    else:
        store = workspace_store

    if mcp_manager is None:
        from backend.app.api.workspace import get_mcp_manager
        mgr = get_mcp_manager()
    else:
        mgr = mcp_manager

    conn_id = tool_name.split("__", 1)[0] if "__" in tool_name else ""
    bare_name = tool_name.split("__", 1)[1] if "__" in tool_name else tool_name

    conn_obj = store.get_connector(conn_id, mask=False) if store else None
    policy = conn_obj.approval_policy if conn_obj and conn_obj.approval_policy else "auto_allow_readonly"

    tool_meta = next(
        (t for t in active_tools if t.get("namespaced_name") == tool_name or t.get("name") == bare_name),
        {},
    )
    read_only_hint = tool_meta.get("read_only_hint")
    destructive_hint = tool_meta.get("destructive_hint")

    decision = gate.evaluate(
        connector_id=conn_id,
        tool_name=bare_name,
        policy=policy,
        read_only_hint=read_only_hint,
        destructive_hint=destructive_hint,
    )

    approval_id = None
    if decision == "ask":
        approval_id = f"apr_{uuid.uuid4().hex[:8]}"
        gate.create_pending(
            approval_id,
            connector_id=conn_id,
            tool_name=bare_name,
            namespaced_name=tool_name,
            arguments=tool_args,
        )
        yield ToolApprovalRequiredEvent(
            metadata={
                "approval_id": approval_id,
                "connector_id": conn_id,
                "tool_name": bare_name,
                "namespaced_name": tool_name,
                "arguments": tool_args,
                "approval_policy": policy,
                "read_only_hint": read_only_hint,
                "destructive_hint": destructive_hint,
            }
        )
        wait_task = asyncio.create_task(
            gate.wait_for_decision(approval_id, timeout=approval_timeout, cancel_event=cancel_event)
        )
        last_keepalive = time.monotonic()
        try:
            while not wait_task.done():
                if cancel_event.is_set():
                    break
                done, _ = await asyncio.wait([wait_task], timeout=0.25)
                if done:
                    break
                now_m = time.monotonic()
                if now_m - last_keepalive >= keepalive_interval:
                    last_keepalive = now_m
                    yield KeepaliveEvent()
            if cancel_event.is_set():
                outcome.cancelled = True
                outcome.denied = True
                return
            decision = await wait_task
        finally:
            if not wait_task.done():
                wait_task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await wait_task
            if approval_id and hasattr(gate, "_pending") and approval_id in gate._pending:
                gate._pending.pop(approval_id, None)

    if cancel_event.is_set():
        outcome.cancelled = True
        outcome.denied = True
        return

    if decision == "deny":
        outcome.denied = True
        rec = {
            "id": f"call_{uuid.uuid4().hex[:8]}",
            "name": tool_name,
            "namespaced_name": tool_name,
            "connector_id": conn_id,
            "tool_name": bare_name,
            "arguments": tool_args,
            "result": "Tool execution denied by user.",
            "is_error": True,
            "ok": False,
            "status": "denied",
            "duration_ms": 0,
        }
        outcome.record = rec
        outcome.followup_prompt = (
            f"{effective_prompt}\n\n"
            f"Tool Execution `{tool_name}` was denied by user.\n\n"
            "Please complete your response acknowledging the denial."
        )
        yield ToolCallEvent(metadata={"tool_calls": [rec]})
        return

    tracer = get_tracer("gemini_local_ui.tools")
    with tracer.start_as_current_span("mcp.tool.execute") as span:
        span.set_attribute("tool.name", tool_name)
        span.set_attribute("tool.connector_id", conn_id)
        tool_res = await mgr.call_tool(tool_name, tool_args)
        is_err = bool(tool_res.get("is_error", False))
        span.set_attribute("tool.is_error", is_err)

    rec = {
        "id": f"call_{uuid.uuid4().hex[:8]}",
        "name": tool_name,
        "namespaced_name": tool_name,
        "connector_id": conn_id,
        "tool_name": bare_name,
        "arguments": tool_args,
        "result": tool_res.get("result", ""),
        "is_error": is_err,
        "ok": not is_err,
        "status": "error" if is_err else "ok",
        "duration_ms": tool_res.get("duration_ms", 0),
    }
    outcome.record = rec
    outcome.followup_prompt = (
        f"{effective_prompt}\n\n"
        f"Tool Execution `{tool_name}` Result:\n{tool_res.get('result', '')}\n\n"
        "Please complete your response based on this tool result."
    )
    yield ToolCallEvent(metadata={"tool_calls": [rec]})
