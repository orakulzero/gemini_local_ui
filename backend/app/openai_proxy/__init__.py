"""Compatibility alias package re-exporting backend.app.openai."""
from backend.app.openai import *
from backend.app.openai import adapter, routes, schemas, sse

__all__ = [
    "router",
    "OpenAIAdapter",
    "ChatCompletionRequest",
    "ChatCompletionResponse",
    "ChatCompletionChoice",
    "ChatCompletionChunk",
    "OpenAIModel",
    "OpenAIModelList",
    "stream_openai_sse",
    "format_non_streaming_response",
    "adapter",
    "routes",
    "schemas",
    "sse",
]
