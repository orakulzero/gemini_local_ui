"""
backend/app/anthropic/__init__.py
Anthropic Messages API compatibility package for Gemini Local UI.
"""

from backend.app.anthropic.adapter import AnthropicAdapter
from backend.app.anthropic.routes import router as anthropic_router
from backend.app.anthropic.schemas import (
    CountTokensRequest,
    CountTokensResponse,
    ImageBlock,
    MessageParam,
    MessageRequest,
    MessageResponse,
    TextBlock,
    ThinkingBlock,
    ToolParam,
    ToolResultBlock,
    ToolUseBlock,
)

__all__ = [
    "anthropic_router",
    "AnthropicAdapter",
    "MessageRequest",
    "MessageResponse",
    "MessageParam",
    "TextBlock",
    "ImageBlock",
    "ThinkingBlock",
    "ToolUseBlock",
    "ToolResultBlock",
    "ToolParam",
    "CountTokensRequest",
    "CountTokensResponse",
]
