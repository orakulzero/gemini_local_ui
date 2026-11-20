"""
backend/app/services/stream_events.py
Typed streaming events and SSE frame formatting for chat generation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from typing import Any, Union


def format_ui_sse(event_data: dict[str, Any]) -> str:
    """Format dictionary into compact UI SSE event string."""
    return f"data: {json.dumps(event_data, separators=(',', ':'))}\n\n"


@dataclass
class StartEvent:
    conversation_id: str
    user_message_id: int | str
    trace_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "type": "start",
            "conversation_id": self.conversation_id,
            "user_message_id": self.user_message_id,
        }
        if self.trace_id:
            d["trace_id"] = self.trace_id
        return d

    def to_sse_frame(self) -> str:
        return format_ui_sse(self.to_dict())


@dataclass
class ThoughtEvent:
    delta: str

    def to_dict(self) -> dict[str, Any]:
        return {"type": "thought", "delta": self.delta}

    def to_sse_frame(self) -> str:
        return format_ui_sse(self.to_dict())


@dataclass
class TextEvent:
    delta: str

    def to_dict(self) -> dict[str, Any]:
        return {"type": "text", "delta": self.delta}

    def to_sse_frame(self) -> str:
        return format_ui_sse(self.to_dict())


@dataclass
class FailoverEvent:
    payload: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        d = dict(self.payload)
        d["type"] = "failover"
        return d

    def to_sse_frame(self) -> str:
        return format_ui_sse(self.to_dict())


@dataclass
class CitationEvent:
    metadata: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {"type": "citation", "metadata": dict(self.metadata)}

    def to_sse_frame(self) -> str:
        return format_ui_sse(self.to_dict())


@dataclass
class GroundingEvent:
    metadata: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {"type": "grounding", "metadata": dict(self.metadata)}

    def to_sse_frame(self) -> str:
        return format_ui_sse(self.to_dict())


@dataclass
class MediaEvent:
    metadata: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {"type": "media", "metadata": dict(self.metadata)}

    def to_sse_frame(self) -> str:
        return format_ui_sse(self.to_dict())


@dataclass
class ToolApprovalRequiredEvent:
    metadata: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {"type": "tool_approval_required", "metadata": dict(self.metadata)}

    def to_sse_frame(self) -> str:
        return format_ui_sse(self.to_dict())


@dataclass
class ToolCallEvent:
    metadata: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {"type": "tool_call", "metadata": dict(self.metadata)}

    def to_sse_frame(self) -> str:
        return format_ui_sse(self.to_dict())


@dataclass
class DoneEvent:
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"type": "done", "metadata": dict(self.metadata)}

    def to_sse_frame(self) -> str:
        return format_ui_sse(self.to_dict())


@dataclass
class SavedEvent:
    assistant_message_id: int | str

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": "saved",
            "assistant_message_id": self.assistant_message_id,
        }

    def to_sse_frame(self) -> str:
        return format_ui_sse(self.to_dict())


@dataclass
class ErrorEvent:
    error: str

    def to_dict(self) -> dict[str, Any]:
        return {"type": "error", "error": self.error}

    def to_sse_frame(self) -> str:
        return format_ui_sse(self.to_dict())


@dataclass
class KeepaliveEvent:
    def to_dict(self) -> dict[str, Any]:
        return {"type": "keepalive"}

    def to_sse_frame(self) -> str:
        return ": keepalive\n\n"


ChatStreamEvent = Union[
    StartEvent,
    ThoughtEvent,
    TextEvent,
    FailoverEvent,
    CitationEvent,
    GroundingEvent,
    MediaEvent,
    ToolApprovalRequiredEvent,
    ToolCallEvent,
    DoneEvent,
    SavedEvent,
    ErrorEvent,
    KeepaliveEvent,
]


async def stream_with_disconnect_check(
    gen: Any,
    request: Any,
    timeout: float = 1.0,
    max_queue_size: int = 64,
) -> Any:
    """Single-coroutine disconnect-aware stream wrapper (Starlette CancelScope safe)."""
    import asyncio
    import contextlib

    _ = max_queue_size
    anext_task: asyncio.Task[Any] | None = None
    try:
        while True:
            if await request.is_disconnected():
                break
            if anext_task is None:
                anext_task = asyncio.create_task(gen.__anext__())
            done, _ = await asyncio.wait({anext_task}, timeout=timeout)
            if done:
                try:
                    val = anext_task.result()
                except StopAsyncIteration:
                    anext_task = None
                    break
                anext_task = None
                yield val
    finally:
        if anext_task is not None and not anext_task.done():
            anext_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, StopAsyncIteration, Exception):
                await anext_task
        aclose_fn = getattr(gen, "aclose", None)
        if callable(aclose_fn):
            with contextlib.suppress(Exception):
                await aclose_fn()
