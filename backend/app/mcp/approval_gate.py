"""
backend/app/mcp/approval_gate.py
Human-in-the-loop (HITL) approval gate for MCP tool calls.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Literal, Optional, Tuple

logger = logging.getLogger("gemini_mcp.approval_gate")

DecisionType = Literal["allow_once", "allow_session", "deny"]
EvaluationResult = Literal["allow", "ask", "deny"]
MAX_PENDING_APPROVALS = 64


class PendingApproval:
    def __init__(
        self,
        approval_id: str,
        connector_id: str,
        tool_name: str,
        namespaced_name: str,
        arguments: Dict[str, Any],
        loop: Optional[asyncio.AbstractEventLoop] = None,
        event: Optional[asyncio.Event] = None,
    ) -> None:
        self.approval_id = approval_id
        self.connector_id = connector_id
        self.tool_name = tool_name
        self.namespaced_name = namespaced_name
        self.arguments = arguments
        self.loop = loop
        self.event = event
        self.decision: Optional[DecisionType] = None


class ToolApprovalGate:
    def __init__(self) -> None:
        self._pending: Dict[str, PendingApproval] = {}
        self._session_allowlist: set[Tuple[str, str]] = set()

    def evaluate(
        self,
        connector_id: str,
        tool_name: str,
        policy: str = "auto_allow_readonly",
        read_only_hint: Optional[bool] = None,
        destructive_hint: Optional[bool] = None,
        session_id: Optional[str] = None,
    ) -> EvaluationResult:
        if policy == "deny":
            return "deny"
        if policy == "auto_allow_all":
            return "allow"
        if (connector_id, tool_name) in self._session_allowlist:
            return "allow"
        if policy == "always_ask":
            return "ask"
        # auto_allow_readonly: allow only if known read-only and non-destructive
        if read_only_hint is True and destructive_hint is not True:
            return "allow"
        return "ask"

    def create_pending(
        self,
        approval_id: str,
        connector_id: str,
        tool_name: str,
        namespaced_name: str,
        arguments: Dict[str, Any],
    ) -> PendingApproval:
        if len(self._pending) >= MAX_PENDING_APPROVALS:
            oldest_key = next(iter(self._pending))
            oldest = self._pending.pop(oldest_key)
            if oldest.event and not oldest.event.is_set():
                oldest.decision = "deny"
                oldest.event.set()

        loop: Optional[asyncio.AbstractEventLoop] = None
        event: Optional[asyncio.Event] = None
        try:
            loop = asyncio.get_running_loop()
            event = asyncio.Event()
        except RuntimeError:
            loop = None
            event = None

        pending = PendingApproval(
            approval_id=approval_id,
            connector_id=connector_id,
            tool_name=tool_name,
            namespaced_name=namespaced_name,
            arguments=arguments,
            loop=loop,
            event=event,
        )
        self._pending[approval_id] = pending
        return pending

    def submit_decision(self, approval_id: str, decision: DecisionType) -> bool:
        pending = self._pending.get(approval_id)
        if not pending:
            return False
        pending.decision = decision
        if decision == "allow_session":
            self._session_allowlist.add((pending.connector_id, pending.tool_name))
        if pending.event is not None:
            if pending.loop and pending.loop.is_running():
                pending.loop.call_soon_threadsafe(pending.event.set)
            else:
                pending.event.set()
        return True

    async def wait_for_decision(
        self,
        approval_id: str,
        timeout: float = 120.0,
        cancel_event: Optional[asyncio.Event] = None,
    ) -> DecisionType:
        pending = self._pending.get(approval_id)
        if not pending:
            return "deny"
        if pending.decision is not None:
            self._pending.pop(approval_id, None)
            return pending.decision
        if pending.event is None:
            pending.event = asyncio.Event()
            pending.loop = asyncio.get_running_loop()

        decision_fut = asyncio.create_task(pending.event.wait())
        tasks = {decision_fut}
        cancel_task = None
        if cancel_event is not None:
            cancel_task = asyncio.create_task(cancel_event.wait())
            tasks.add(cancel_task)

        try:
            done, pending_tasks = await asyncio.wait(
                tasks,
                timeout=timeout,
                return_when=asyncio.FIRST_COMPLETED,
            )
            for t in pending_tasks:
                t.cancel()

            if cancel_task and cancel_task in done:
                return "deny"

            if decision_fut in done:
                return pending.decision or "deny"

            return "deny"
        finally:
            self._pending.pop(approval_id, None)


_global_gate: Optional[ToolApprovalGate] = None


def get_approval_gate() -> ToolApprovalGate:
    global _global_gate
    if _global_gate is None:
        _global_gate = ToolApprovalGate()
    return _global_gate
