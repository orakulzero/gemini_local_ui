"""
backend/app/openai/schemas.py
Pydantic v2 models conforming to OpenAI Chat Completions and Models specifications.
"""

from __future__ import annotations

import time
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field


# ============================================================================
# 1. Models List Schemas (/v1/models)
# ============================================================================

class ModelPermission(BaseModel):
    id: str = Field(default_factory=lambda: f"modelperm-{int(time.time())}")
    object: str = "model_permission"
    created: int = Field(default_factory=lambda: int(time.time()))
    allow_create_engine: bool = False
    allow_sampling: bool = True
    allow_logprobs: bool = True
    allow_search_indices: bool = False
    allow_view: bool = True
    allow_fine_tuning: bool = False
    organization: str = "*"
    group: str | None = None
    is_blocking: bool = False

    model_config = ConfigDict(extra="ignore")


class OpenAIModel(BaseModel):
    id: str
    object: str = "model"
    created: int = Field(default_factory=lambda: 1700000000)
    owned_by: str = "google-gemini"
    permission: list[ModelPermission] = Field(default_factory=list)
    root: str | None = None
    parent: str | None = None

    model_config = ConfigDict(extra="ignore")


class OpenAIModelList(BaseModel):
    object: str = "list"
    data: list[OpenAIModel]

    model_config = ConfigDict(extra="ignore")


# ============================================================================
# 2. Chat Completions Request Schemas (/v1/chat/completions)
# ============================================================================

class ImageUrlDetail(BaseModel):
    url: str
    detail: Literal["auto", "low", "high"] | None = "auto"

    model_config = ConfigDict(extra="ignore")


class ContentPart(BaseModel):
    type: Literal["text", "image_url"]
    text: str | None = None
    image_url: str | ImageUrlDetail | None = None

    model_config = ConfigDict(extra="ignore")


class ChatCompletionMessageParam(BaseModel):
    role: Literal["system", "developer", "user", "assistant", "tool", "function"]
    content: str | list[ContentPart | dict[str, Any] | str] | None = None
    name: str | None = None
    tool_calls: list[dict[str, Any]] | None = None
    tool_call_id: str | None = None

    model_config = ConfigDict(extra="ignore")


class ChatCompletionRequest(BaseModel):
    model: str
    messages: list[ChatCompletionMessageParam]
    temperature: float | None = 1.0
    top_p: float | None = 1.0
    n: int | None = 1
    stream: bool | None = False
    stop: str | list[str] | None = None
    max_tokens: int | None = None
    max_completion_tokens: int | None = None
    presence_penalty: float | None = 0.0
    frequency_penalty: float | None = 0.0
    user: str | None = None
    response_format: dict[str, Any] | None = None
    tools: list[dict[str, Any]] | None = None
    tool_choice: str | dict[str, Any] | None = None
    account_id: str | None = None
    feature_mode: str | None = None
    gem_id: str | None = None
    extensions: list[str] | None = None

    model_config = ConfigDict(extra="ignore")


# ============================================================================
# 3. Chat Completions Response Schemas (Non-Streaming)
# ============================================================================

class ChatCompletionUsage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

    model_config = ConfigDict(extra="ignore")


class ChatCompletionResponseMessage(BaseModel):
    role: str = "assistant"
    content: str | None = None
    tool_calls: list[dict[str, Any]] | None = None

    model_config = ConfigDict(extra="ignore")


class ChatCompletionChoice(BaseModel):
    index: int = 0
    message: ChatCompletionResponseMessage
    finish_reason: Literal["stop", "length", "content_filter", "tool_calls"] | None = "stop"

    model_config = ConfigDict(extra="ignore")


class ChatCompletionResponse(BaseModel):
    id: str
    object: str = "chat.completion"
    created: int
    model: str
    choices: list[ChatCompletionChoice]
    usage: ChatCompletionUsage

    model_config = ConfigDict(extra="ignore")


# ============================================================================
# 4. Chat Completions Stream Chunk Schemas (Streaming SSE)
# ============================================================================

class ChatCompletionChunkDelta(BaseModel):
    role: str | None = None
    content: str | None = None
    tool_calls: list[dict[str, Any]] | None = None

    model_config = ConfigDict(extra="ignore")


class ChatCompletionChunkChoice(BaseModel):
    index: int = 0
    delta: ChatCompletionChunkDelta
    finish_reason: Literal["stop", "length", "content_filter", "tool_calls"] | None = None

    model_config = ConfigDict(extra="ignore")


class ChatCompletionChunk(BaseModel):
    id: str
    object: str = "chat.completion.chunk"
    created: int
    model: str
    choices: list[ChatCompletionChunkChoice]

    model_config = ConfigDict(extra="ignore")


# ============================================================================
# 5. OpenAI Standard Error Schema
# ============================================================================

class OpenAIErrorDetail(BaseModel):
    message: str
    type: str = "invalid_request_error"
    param: str | None = None
    code: str | None = None


class OpenAIErrorResponse(BaseModel):
    error: OpenAIErrorDetail
