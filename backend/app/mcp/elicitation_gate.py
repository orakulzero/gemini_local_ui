"""
backend/app/mcp/elicitation_gate.py
MCP 2026-07-28 Elicitation Gate supporting `form` and `url` modes plus MRTR.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any, Dict, Literal, Optional, Tuple
from urllib.parse import urlsplit

logger = logging.getLogger("gemini_mcp.elicitation_gate")

ElicitationAction = Literal["accept", "decline", "cancel"]
ElicitationMode = Literal["form", "url"]
MAX_PENDING_ELICITATIONS = 64


def validate_elicitation_url(url: str) -> str:
    """Allow ONLY https:// (with hostname) or loopback http://127.0.0.1 / http://localhost."""
    if not isinstance(url, str) or not url.strip():
        raise ValueError("Elicitation URL must be a non-empty string")
    cleaned = url.strip()
    parsed = urlsplit(cleaned)
    scheme = (parsed.scheme or "").lower()
    hostname = (parsed.hostname or "").lower()
    if scheme == "https" and hostname:
        return cleaned
    if scheme == "http" and hostname in ("127.0.0.1", "localhost"):
        return cleaned
    raise ValueError(f"Unsafe elicitation URL scheme or host: {cleaned[:64]}")


class PendingElicitation:
    def __init__(
        self, elicitation_id: str, *, connector_id: str, mode: ElicitationMode = "form",
        message: str = "", requested_schema: Optional[Dict[str, Any]] = None,
        url: Optional[str] = None, tool_name: Optional[str] = None,
        loop: Optional[asyncio.AbstractEventLoop] = None, event: Optional[asyncio.Event] = None,
    ) -> None:
        self.elicitation_id, self.connector_id, self.mode = elicitation_id, connector_id, mode
        self.message, self.requested_schema, self.url, self.tool_name = message, requested_schema, url, tool_name
        self.loop, self.event = loop, event
        self.action: Optional[ElicitationAction] = None
        self.content: Optional[Dict[str, Any]] = None

    def to_metadata(self) -> Dict[str, Any]:
        return {
            "elicitation_id": self.elicitation_id, "connector_id": self.connector_id,
            "tool_name": self.tool_name, "mode": self.mode, "message": self.message,
            "requested_schema": self.requested_schema, "url": self.url,
        }


class ElicitationGate:
    def __init__(self) -> None:
        self._pending: Dict[str, PendingElicitation] = {}

    def create_pending(
        self,
        elicitation_id: str,
        *,
        connector_id: str,
        mode: ElicitationMode = "form",
        message: str = "",
        requested_schema: Optional[Dict[str, Any]] = None,
        url: Optional[str] = None,
        tool_name: Optional[str] = None,
    ) -> PendingElicitation:
        safe_url: Optional[str] = None
        if mode == "url" or url is not None:
            safe_url = validate_elicitation_url(url or "")

        if len(self._pending) >= MAX_PENDING_ELICITATIONS:
            oldest_key = next(iter(self._pending))
            oldest = self._pending.pop(oldest_key)
            if oldest.event and not oldest.event.is_set():
                oldest.action = "cancel"
                oldest.event.set()

        loop: Optional[asyncio.AbstractEventLoop] = None
        event: Optional[asyncio.Event] = None
        try:
            loop = asyncio.get_running_loop()
            event = asyncio.Event()
        except RuntimeError:
            pass

        pending = PendingElicitation(
            elicitation_id,
            connector_id=connector_id,
            mode=mode,
            message=message,
            requested_schema=requested_schema,
            url=safe_url,
            tool_name=tool_name,
            loop=loop,
            event=event,
        )
        self._pending[elicitation_id] = pending
        return pending

    def submit_response(
        self,
        elicitation_id: str,
        action: ElicitationAction,
        content: Optional[Dict[str, Any]] = None,
    ) -> bool:
        pending = self._pending.get(elicitation_id)
        if not pending:
            return False
        if action not in ("accept", "decline", "cancel"):
            raise ValueError(f"Invalid elicitation action: {action}")
        pending.action = action
        pending.content = dict(content) if (action == "accept" and isinstance(content, dict)) else None
        if pending.event is not None:
            if pending.loop and pending.loop.is_running():
                pending.loop.call_soon_threadsafe(pending.event.set)
            else:
                pending.event.set()
        return True

    async def wait_for_response(
        self,
        elicitation_id: str,
        timeout: float = 120.0,
        cancel_event: Optional[asyncio.Event] = None,
    ) -> Tuple[ElicitationAction, Optional[Dict[str, Any]]]:
        pending = self._pending.get(elicitation_id)
        if not pending:
            return ("cancel", None)
        if pending.action is not None:
            self._pending.pop(elicitation_id, None)
            return (pending.action, pending.content)
        if pending.event is None:
            pending.loop = asyncio.get_running_loop()
            pending.event = asyncio.Event()

        decision_fut = asyncio.create_task(pending.event.wait())
        tasks = {decision_fut}
        cancel_task: Optional[asyncio.Task[Any]] = None
        if cancel_event is not None:
            cancel_task = asyncio.create_task(cancel_event.wait())
            tasks.add(cancel_task)

        try:
            done, still_pending = await asyncio.wait(tasks, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
            for t in still_pending:
                t.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await t
            if cancel_task and cancel_task in done:
                return ("cancel", None)
            if decision_fut in done and pending.action is not None:
                return (pending.action, pending.content)
            return ("cancel", None)
        finally:
            self._pending.pop(elicitation_id, None)


_global_elicitation_gate: Optional[ElicitationGate] = None


def get_elicitation_gate() -> ElicitationGate:
    global _global_elicitation_gate
    if _global_elicitation_gate is None:
        _global_elicitation_gate = ElicitationGate()
    return _global_elicitation_gate
