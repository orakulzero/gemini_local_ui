"""
backend/app/services/tool_runtime.py
MCP tool block parsing, human-in-the-loop (HITL) approval gating, and execution runtime.
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
import inspect
import json
import logging
import re
import time
from typing import Any, AsyncIterator
from unittest.mock import NonCallableMock
import uuid

from backend.app.observability.telemetry import get_tracer
from backend.app.services.stream_events import (
    ChatStreamEvent,
    ElicitationRequiredEvent,
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
    elicitation_gate: Any = None,
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
    elicit_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

    async def _on_elicit_event(meta: dict[str, Any]) -> None:
        await elicit_queue.put(meta)

    async def _run_single_call(args_to_send: dict[str, Any]) -> dict[str, Any]:
        with tracer.start_as_current_span("mcp.tool.execute") as span:
            span.set_attribute("tool.name", tool_name)
            span.set_attribute("tool.connector_id", conn_id)
            fn = mgr.call_tool
            supports_elicit = False
            if not isinstance(fn, NonCallableMock):
                with contextlib.suppress(Exception):
                    supports_elicit = "on_elicitation" in inspect.signature(fn).parameters
            if supports_elicit:
                res = await fn(
                    tool_name,
                    args_to_send,
                    on_elicitation=_on_elicit_event,
                    cancel_event=cancel_event,
                )
            else:
                res = await fn(tool_name, args_to_send)
            span.set_attribute("tool.is_error", bool(res.get("is_error", False)))
            return res

    async def _poll_call_with_elicitations(args_to_send: dict[str, Any]) -> AsyncIterator[ChatStreamEvent | dict[str, Any]]:
        call_task = asyncio.create_task(_run_single_call(args_to_send))
        last_ka = time.monotonic()
        try:
            while not call_task.done():
                while not elicit_queue.empty():
                    yield ElicitationRequiredEvent(metadata=elicit_queue.get_nowait())
                if cancel_event.is_set():
                    break
                await asyncio.wait({call_task}, timeout=0.25)
                while not elicit_queue.empty():
                    yield ElicitationRequiredEvent(metadata=elicit_queue.get_nowait())
                now_m = time.monotonic()
                if not call_task.done() and now_m - last_ka >= keepalive_interval:
                    last_ka = now_m
                    yield KeepaliveEvent()
            if cancel_event.is_set() and not call_task.done():
                outcome.cancelled = True
                outcome.denied = True
                return
            yield await call_task
        finally:
            if not call_task.done():
                call_task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await call_task

    tool_res: dict[str, Any] = {}
    async for item in _poll_call_with_elicitations(tool_args):
        if isinstance(item, dict):
            tool_res = item
        else:
            yield item
    if outcome.cancelled or cancel_event.is_set():
        outcome.cancelled = True
        outcome.denied = True
        return

    is_err = bool(tool_res.get("is_error", False))
    mrtr_data = tool_res.get("structured_content")
    if not isinstance(mrtr_data, dict):
        raw_res_str = tool_res.get("result")
        if isinstance(raw_res_str, str) and raw_res_str.strip().startswith("{"):
            with contextlib.suppress(Exception):
                parsed = json.loads(raw_res_str.strip())
                if isinstance(parsed, dict):
                    mrtr_data = parsed

    final_args = dict(tool_args)
    status_override: str | None = None
    if not is_err and isinstance(mrtr_data, dict) and mrtr_data.get("resultType") == "input_required":
        from backend.app.mcp.elicitation_gate import get_elicitation_gate
        egate = elicitation_gate or get_elicitation_gate()
        elicit_id = str(mrtr_data.get("elicitationId") or mrtr_data.get("elicitation_id") or f"elicit_{uuid.uuid4().hex[:10]}")
        e_mode = "url" if mrtr_data.get("mode") == "url" else "form"
        e_msg = str(mrtr_data.get("message") or "")
        e_schema = mrtr_data.get("requestedSchema") or mrtr_data.get("requested_schema")
        e_url = mrtr_data.get("url")
        try:
            pending_el = egate.create_pending(
                elicit_id,
                connector_id=conn_id,
                mode=e_mode,  # type: ignore[arg-type]
                message=e_msg,
                requested_schema=e_schema if isinstance(e_schema, dict) else None,
                url=str(e_url) if e_url is not None else None,
                tool_name=bare_name,
            )
            yield ElicitationRequiredEvent(metadata=pending_el.to_metadata())
            ewait = asyncio.create_task(
                egate.wait_for_response(elicit_id, timeout=approval_timeout, cancel_event=cancel_event)
            )
            last_ka = time.monotonic()
            try:
                while not ewait.done():
                    if cancel_event.is_set():
                        break
                    done_set, _ = await asyncio.wait({ewait}, timeout=0.25)
                    if done_set:
                        break
                    if time.monotonic() - last_ka >= keepalive_interval:
                        last_ka = time.monotonic()
                        yield KeepaliveEvent()
                if cancel_event.is_set():
                    outcome.cancelled = True
                    outcome.denied = True
                    return
                e_action, e_content = await ewait
            finally:
                if not ewait.done():
                    ewait.cancel()
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        await ewait
                egate._pending.pop(elicit_id, None)
        except ValueError:
            e_action, e_content = "cancel", None

        if e_action == "accept":
            final_args = {**tool_args, **(e_content or {})}
            async for item in _poll_call_with_elicitations(final_args):
                if isinstance(item, dict):
                    tool_res = item
                else:
                    yield item
            if outcome.cancelled or cancel_event.is_set():
                outcome.cancelled = True
                outcome.denied = True
                return
            is_err = bool(tool_res.get("is_error", False))
        else:
            is_err = True
            status_override = "declined" if e_action == "decline" else "cancelled"
            tool_res = {
                "result": f"Tool elicitation {status_override} by user.",
                "is_error": True,
                "duration_ms": tool_res.get("duration_ms", 0),
            }

    rec = {
        "id": f"call_{uuid.uuid4().hex[:8]}",
        "name": tool_name,
        "namespaced_name": tool_name,
        "connector_id": conn_id,
        "tool_name": bare_name,
        "arguments": final_args,
        "result": tool_res.get("result", ""),
        "is_error": is_err,
        "ok": not is_err,
        "status": status_override or ("error" if is_err else "ok"),
        "duration_ms": tool_res.get("duration_ms", 0),
    }
    outcome.record = rec
    outcome.followup_prompt = (
        f"{effective_prompt}\n\n"
        f"Tool Execution `{tool_name}` Result:\n{tool_res.get('result', '')}\n\n"
        "Please complete your response based on this tool result."
    )
    yield ToolCallEvent(metadata={"tool_calls": [rec]})
