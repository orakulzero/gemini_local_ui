"""
backend/app/anthropic/schemas.py
Pydantic data models for the Anthropic Messages API (v1 / 2023-06-01).
Covers request validation, multimodal content blocks, tool use, extended thinking,
usage accounting, and streaming event payloads.
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional, Union
from pydantic import BaseModel, ConfigDict, Field


# ----------------------------------------------------------------------------
# Content Blocks
# ----------------------------------------------------------------------------

class TextBlock(BaseModel):
    type: Literal["text"] = "text"
    text: str
    cache_control: Optional[Dict[str, Any]] = None

    model_config = ConfigDict(extra="ignore")


class ImageSource(BaseModel):
    type: Literal["base64", "url"]
    media_type: Optional[str] = "image/png"
    data: Optional[str] = None
    url: Optional[str] = None

    model_config = ConfigDict(extra="ignore")


class ImageBlock(BaseModel):
    type: Literal["image"] = "image"
    source: ImageSource
    cache_control: Optional[Dict[str, Any]] = None

    model_config = ConfigDict(extra="ignore")


class ThinkingBlock(BaseModel):
    type: Literal["thinking"] = "thinking"
    thinking: str
    signature: Optional[str] = None

    model_config = ConfigDict(extra="ignore")


class RedactedThinkingBlock(BaseModel):
    type: Literal["redacted_thinking"] = "redacted_thinking"
    data: str

    model_config = ConfigDict(extra="ignore")


class ToolUseBlock(BaseModel):
    type: Literal["tool_use"] = "tool_use"
    id: str
    name: str
    input: Dict[str, Any]

    model_config = ConfigDict(extra="ignore")


class ToolResultBlock(BaseModel):
    type: Literal["tool_result"] = "tool_result"
    tool_use_id: str
    content: Optional[Union[str, List[Union[TextBlock, ImageBlock, Dict[str, Any]]]]] = None
    is_error: Optional[bool] = False
    cache_control: Optional[Dict[str, Any]] = None

    model_config = ConfigDict(extra="ignore")


ContentBlock = Union[
    TextBlock,
    ImageBlock,
    ThinkingBlock,
    RedactedThinkingBlock,
    ToolUseBlock,
    ToolResultBlock,
    Dict[str, Any],
]


# ----------------------------------------------------------------------------
# Messages & Tools Parameters
# ----------------------------------------------------------------------------

class MessageParam(BaseModel):
    role: Literal["user", "assistant", "system", "developer"]
    content: Union[str, List[ContentBlock]]

    model_config = ConfigDict(extra="ignore")


class ToolParam(BaseModel):
    name: str = ""
    type: Optional[str] = None
    description: Optional[str] = ""
    input_schema: Optional[Dict[str, Any]] = Field(default_factory=dict)
    cache_control: Optional[Dict[str, Any]] = None

    model_config = ConfigDict(extra="ignore")


class ToolChoiceParam(BaseModel):
    type: str = "auto"
    name: Optional[str] = None
    disable_parallel_tool_use: Optional[bool] = None

    model_config = ConfigDict(extra="ignore")


# ----------------------------------------------------------------------------
# Message Request
# ----------------------------------------------------------------------------

class MessageRequest(BaseModel):
    model: str
    messages: List[MessageParam]
    max_tokens: Optional[int] = 8192
    system: Optional[Union[str, List[Union[TextBlock, Dict[str, Any]]]]] = None
    metadata: Optional[Dict[str, Any]] = None
    stop_sequences: Optional[List[str]] = None
    stream: Optional[bool] = False
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    top_k: Optional[int] = None
    tools: Optional[List[ToolParam]] = None
    tool_choice: Optional[Union[str, ToolChoiceParam, Dict[str, Any]]] = None
    thinking: Optional[Dict[str, Any]] = None
    account_id: Optional[str] = None
    gem_id: Optional[str] = None
    extensions: Optional[List[str]] = None

    model_config = ConfigDict(extra="ignore")


# ----------------------------------------------------------------------------
# Message Response & Usage
# ----------------------------------------------------------------------------

class MessageUsage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: Optional[int] = 0
    cache_read_input_tokens: Optional[int] = 0


class MessageResponse(BaseModel):
    id: str
    type: Literal["message"] = "message"
    role: Literal["assistant"] = "assistant"
    content: List[ContentBlock]
    model: str
    stop_reason: Optional[Literal["end_turn", "max_tokens", "stop_sequence", "tool_use"]] = "end_turn"
    stop_sequence: Optional[str] = None
    usage: MessageUsage


# ----------------------------------------------------------------------------
# Token Counting API
# ----------------------------------------------------------------------------

class CountTokensRequest(BaseModel):
    model: str
    messages: List[MessageParam]
    system: Optional[Union[str, List[Union[TextBlock, Dict[str, Any]]]]] = None
    tools: Optional[List[ToolParam]] = None
    tool_choice: Optional[Union[str, ToolChoiceParam, Dict[str, Any]]] = None
    thinking: Optional[Dict[str, Any]] = None

    model_config = ConfigDict(extra="ignore")


class CountTokensResponse(BaseModel):
    input_tokens: int
