"""
backend/app/anthropic/schemas.py
Pydantic data models for the Anthropic Messages API (v1 / 2023-06-01).
Covers request validation, multimodal content blocks, tool use, extended thinking,
usage accounting, and streaming event payloads.
"""

from __future__ import annotations

from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field


# ----------------------------------------------------------------------------
# Content Blocks
# ----------------------------------------------------------------------------

class TextBlock(BaseModel):
    type: Literal["text"] = "text"
    text: str
    cache_control: dict[str, Any] | None = None

    model_config = ConfigDict(extra="ignore")


class ImageSource(BaseModel):
    type: Literal["base64", "url"]
    media_type: str | None = "image/png"
    data: str | None = None
    url: str | None = None

    model_config = ConfigDict(extra="ignore")


class ImageBlock(BaseModel):
    type: Literal["image"] = "image"
    source: ImageSource
    cache_control: dict[str, Any] | None = None

    model_config = ConfigDict(extra="ignore")


class ThinkingBlock(BaseModel):
    type: Literal["thinking"] = "thinking"
    thinking: str
    signature: str | None = None

    model_config = ConfigDict(extra="ignore")


class RedactedThinkingBlock(BaseModel):
    type: Literal["redacted_thinking"] = "redacted_thinking"
    data: str

    model_config = ConfigDict(extra="ignore")


class ToolUseBlock(BaseModel):
    type: Literal["tool_use"] = "tool_use"
    id: str
    name: str
    input: dict[str, Any]

    model_config = ConfigDict(extra="ignore")


class ToolResultBlock(BaseModel):
    type: Literal["tool_result"] = "tool_result"
    tool_use_id: str
    content: str | list[TextBlock | ImageBlock | dict[str, Any]] | None = None
    is_error: bool | None = False
    cache_control: dict[str, Any] | None = None

    model_config = ConfigDict(extra="ignore")


ContentBlock = (
    TextBlock
    | ImageBlock
    | ThinkingBlock
    | RedactedThinkingBlock
    | ToolUseBlock
    | ToolResultBlock
    | dict[str, Any]
)


# ----------------------------------------------------------------------------
# Messages & Tools Parameters
# ----------------------------------------------------------------------------

class MessageParam(BaseModel):
    role: Literal["user", "assistant", "system", "developer"]
    content: str | list[ContentBlock]

    model_config = ConfigDict(extra="ignore")


class ToolParam(BaseModel):
    name: str = ""
    type: str | None = None
    description: str | None = ""
    input_schema: dict[str, Any] | None = Field(default_factory=dict)
    cache_control: dict[str, Any] | None = None

    model_config = ConfigDict(extra="ignore")


class ToolChoiceParam(BaseModel):
    type: str = "auto"
    name: str | None = None
    disable_parallel_tool_use: bool | None = None

    model_config = ConfigDict(extra="ignore")


# ----------------------------------------------------------------------------
# Message Request
# ----------------------------------------------------------------------------

class MessageRequest(BaseModel):
    model: str
    messages: list[MessageParam]
    max_tokens: int | None = 8192
    system: str | list[TextBlock | dict[str, Any]] | None = None
    metadata: dict[str, Any] | None = None
    stop_sequences: list[str] | None = None
    stream: bool | None = False
    temperature: float | None = None
    top_p: float | None = None
    top_k: int | None = None
    tools: list[ToolParam] | None = None
    tool_choice: str | ToolChoiceParam | dict[str, Any] | None = None
    thinking: dict[str, Any] | None = None
    account_id: str | None = None
    gem_id: str | None = None
    extensions: list[str] | None = None

    model_config = ConfigDict(extra="ignore")


# ----------------------------------------------------------------------------
# Message Response & Usage
# ----------------------------------------------------------------------------

class MessageUsage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int | None = 0
    cache_read_input_tokens: int | None = 0


class MessageResponse(BaseModel):
    id: str
    type: Literal["message"] = "message"
    role: Literal["assistant"] = "assistant"
    content: list[ContentBlock]
    model: str
    stop_reason: Literal["end_turn", "max_tokens", "stop_sequence", "tool_use"] | None = "end_turn"
    stop_sequence: str | None = None
    usage: MessageUsage


# ----------------------------------------------------------------------------
# Token Counting API
# ----------------------------------------------------------------------------

class CountTokensRequest(BaseModel):
    model: str
    messages: list[MessageParam]
    system: str | list[TextBlock | dict[str, Any]] | None = None
    tools: list[ToolParam] | None = None
    tool_choice: str | ToolChoiceParam | dict[str, Any] | None = None
    thinking: dict[str, Any] | None = None

    model_config = ConfigDict(extra="ignore")


class CountTokensResponse(BaseModel):
    input_tokens: int
