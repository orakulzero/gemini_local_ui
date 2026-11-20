"""
backend/app/contracts/agent_stream_v1.py
Frozen `agy.stream.v1` bidirectional JSON-RPC 2.0 protocol contract (M6b -> M8).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

AGENT_PROTOCOL_VERSION = "agy.stream.v1"

SUPPORTED_CLIENT_METHODS = (
    "session/prompt",
    "session/cancel",
    "session/respond_permission",
    "elicitation/respond",
    "ping",
)

SUPPORTED_EVENT_TYPES = (
    "start",
    "thought",
    "text",
    "failover",
    "citation",
    "grounding",
    "media",
    "tool_approval_required",
    "elicitation_required",
    "tool_call",
    "done",
    "saved",
    "error",
    "keepalive",
)


class WsPromptRequest(BaseModel):
    jsonrpc: Literal["2.0"] = "2.0"
    id: str | int
    method: Literal["session/prompt"] = "session/prompt"
    params: dict[str, Any] = Field(default_factory=dict)


class WsCancelRequest(BaseModel):
    jsonrpc: Literal["2.0"] = "2.0"
    id: str | int | None = None
    method: Literal["session/cancel"] = "session/cancel"
    params: dict[str, Any] = Field(default_factory=dict)


class WsRespondPermissionParams(BaseModel):
    approval_id: str
    decision: Literal["allow_once", "allow_session", "deny"]


class WsRespondPermissionRequest(BaseModel):
    jsonrpc: Literal["2.0"] = "2.0"
    id: str | int | None = None
    method: Literal["session/respond_permission"] = "session/respond_permission"
    params: WsRespondPermissionParams


class WsElicitationRespondParams(BaseModel):
    elicitation_id: str
    action: Literal["accept", "decline", "cancel"]
    content: dict[str, Any] | None = None


class WsElicitationRespondRequest(BaseModel):
    jsonrpc: Literal["2.0"] = "2.0"
    id: str | int | None = None
    method: Literal["elicitation/respond"] = "elicitation/respond"
    params: WsElicitationRespondParams


class WsPingRequest(BaseModel):
    jsonrpc: Literal["2.0"] = "2.0"
    id: str | int
    method: Literal["ping"] = "ping"
    params: dict[str, Any] = Field(default_factory=dict)


class WsSessionUpdateParams(BaseModel):
    protocol_version: str = AGENT_PROTOCOL_VERSION
    trace_id: str = ""
    event: dict[str, Any]


class WsSessionUpdateNotification(BaseModel):
    jsonrpc: Literal["2.0"] = "2.0"
    method: Literal["session/update"] = "session/update"
    params: WsSessionUpdateParams


def build_session_update_frame(event_dict: dict[str, Any], trace_id: str = "") -> dict[str, Any]:
    """Construct canonical Host->Client JSON-RPC 2.0 `session/update` notification."""
    return {
        "jsonrpc": "2.0",
        "method": "session/update",
        "params": {
            "protocol_version": AGENT_PROTOCOL_VERSION,
            "trace_id": trace_id,
            "event": event_dict,
        },
    }


def get_protocol_contract_descriptor() -> dict[str, Any]:
    """Return frozen protocol contract metadata and JSON schemas for codegen/handshake."""
    return {
        "protocol_version": AGENT_PROTOCOL_VERSION,
        "transports": ["sse", "ws-jsonrpc-2.0"],
        "ws_endpoint": "/api/chat/ws",
        "sse_endpoint": "/api/chat/generate",
        "methods": list(SUPPORTED_CLIENT_METHODS),
        "notifications": ["session/update"],
        "event_types": list(SUPPORTED_EVENT_TYPES),
        "schemas": {
            "WsPromptRequest": WsPromptRequest.model_json_schema(),
            "WsCancelRequest": WsCancelRequest.model_json_schema(),
            "WsRespondPermissionRequest": WsRespondPermissionRequest.model_json_schema(),
            "WsElicitationRespondRequest": WsElicitationRespondRequest.model_json_schema(),
            "WsPingRequest": WsPingRequest.model_json_schema(),
            "WsSessionUpdateNotification": WsSessionUpdateNotification.model_json_schema(),
        },
    }
