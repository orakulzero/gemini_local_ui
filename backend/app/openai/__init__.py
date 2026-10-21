"""OpenAI Compatibility API Package."""
from .adapter import OpenAIAdapter
from .routes import router
from .schemas import (
    ChatCompletionChoice,
    ChatCompletionChunk,
    ChatCompletionRequest,
    ChatCompletionResponse,
    OpenAIModel,
    OpenAIModelList,
)
from .sse import format_non_streaming_response, stream_openai_sse

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
]
