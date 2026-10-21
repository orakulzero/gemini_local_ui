"""
backend/app/openai/schemas.py
Pydantic v2 models conforming to OpenAI Chat Completions and Models specifications.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Literal, Optional, Union
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
    group: Optional[str] = None
    is_blocking: bool = False

    model_config = ConfigDict(extra="ignore")


class OpenAIModel(BaseModel):
    id: str
    object: str = "model"
    created: int = Field(default_factory=lambda: 1700000000)
    owned_by: str = "google-gemini"
    permission: List[ModelPermission] = Field(default_factory=list)
    root: Optional[str] = None
    parent: Optional[str] = None

    model_config = ConfigDict(extra="ignore")


class OpenAIModelList(BaseModel):
    object: str = "list"
    data: List[OpenAIModel]

    model_config = ConfigDict(extra="ignore")


# ============================================================================
# 2. Chat Completions Request Schemas (/v1/chat/completions)
# ============================================================================

class ImageUrlDetail(BaseModel):
    url: str
    detail: Optional[Literal["auto", "low", "high"]] = "auto"

    model_config = ConfigDict(extra="ignore")


class ContentPart(BaseModel):
    type: Literal["text", "image_url"]
    text: Optional[str] = None
    image_url: Optional[Union[str, ImageUrlDetail]] = None

    model_config = ConfigDict(extra="ignore")


class ChatCompletionMessageParam(BaseModel):
    role: Literal["system", "developer", "user", "assistant", "tool", "function"]
    content: Optional[Union[str, List[Union[ContentPart, Dict[str, Any], str]]]] = None
    name: Optional[str] = None
    tool_calls: Optional[List[Dict[str, Any]]] = None
    tool_call_id: Optional[str] = None

    model_config = ConfigDict(extra="ignore")


class ChatCompletionRequest(BaseModel):
    model: str
    messages: List[ChatCompletionMessageParam]
    temperature: Optional[float] = 1.0
    top_p: Optional[float] = 1.0
    n: Optional[int] = 1
    stream: Optional[bool] = False
    stop: Optional[Union[str, List[str]]] = None
    max_tokens: Optional[int] = None
    max_completion_tokens: Optional[int] = None
    presence_penalty: Optional[float] = 0.0
    frequency_penalty: Optional[float] = 0.0
    user: Optional[str] = None
    response_format: Optional[Dict[str, Any]] = None
    tools: Optional[List[Dict[str, Any]]] = None
    tool_choice: Optional[Union[str, Dict[str, Any]]] = None
    account_id: Optional[str] = None
    feature_mode: Optional[str] = None
    gem_id: Optional[str] = None
    extensions: Optional[List[str]] = None

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
    content: Optional[str] = None
    tool_calls: Optional[List[Dict[str, Any]]] = None

    model_config = ConfigDict(extra="ignore")


class ChatCompletionChoice(BaseModel):
    index: int = 0
    message: ChatCompletionResponseMessage
    finish_reason: Optional[Literal["stop", "length", "content_filter", "tool_calls"]] = "stop"

    model_config = ConfigDict(extra="ignore")


class ChatCompletionResponse(BaseModel):
    id: str
    object: str = "chat.completion"
    created: int
    model: str
    choices: List[ChatCompletionChoice]
    usage: ChatCompletionUsage

    model_config = ConfigDict(extra="ignore")


# ============================================================================
# 4. Chat Completions Stream Chunk Schemas (Streaming SSE)
# ============================================================================

class ChatCompletionChunkDelta(BaseModel):
    role: Optional[str] = None
    content: Optional[str] = None
    tool_calls: Optional[List[Dict[str, Any]]] = None

    model_config = ConfigDict(extra="ignore")


class ChatCompletionChunkChoice(BaseModel):
    index: int = 0
    delta: ChatCompletionChunkDelta
    finish_reason: Optional[Literal["stop", "length", "content_filter", "tool_calls"]] = None

    model_config = ConfigDict(extra="ignore")


class ChatCompletionChunk(BaseModel):
    id: str
    object: str = "chat.completion.chunk"
    created: int
    model: str
    choices: List[ChatCompletionChunkChoice]

    model_config = ConfigDict(extra="ignore")


# ============================================================================
# 5. OpenAI Standard Error Schema
# ============================================================================

class OpenAIErrorDetail(BaseModel):
    message: str
    type: str = "invalid_request_error"
    param: Optional[str] = None
    code: Optional[str] = None


class OpenAIErrorResponse(BaseModel):
    error: OpenAIErrorDetail
