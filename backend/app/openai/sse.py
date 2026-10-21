"""
backend/app/openai/sse.py
Server-Sent Events (SSE) streaming generator and non-streaming completion formatter.
"""

from __future__ import annotations

import json
import logging
from typing import Any, AsyncIterator, List, Optional
import uuid

from backend.app.bridge.streaming_parser import StreamChunk, sanitize_surrogate_text
from backend.app.openai.adapter import OpenAIAdapter
from backend.app.openai.schemas import (
    ChatCompletionChoice,
    ChatCompletionChunk,
    ChatCompletionChunkChoice,
    ChatCompletionChunkDelta,
    ChatCompletionResponse,
    ChatCompletionResponseMessage,
    ChatCompletionUsage,
)

logger = logging.getLogger("gemini_openai.sse")


def generate_completion_id() -> str:
    """Generates an OpenAI-standard chat completion ID."""
    return f"chatcmpl-{uuid.uuid4().hex[:24]}"


def format_sse_line(data: Any) -> str:
    """Formats an object or string into a standard SSE data payload line."""
    if isinstance(data, str):
        return f"data: {data}\n\n"
    if hasattr(data, "model_dump_json"):
        return f"data: {data.model_dump_json()}\n\n"
    return f"data: {json.dumps(data, separators=(',', ':'))}\n\n"


async def stream_openai_sse(
    stream_generator: AsyncIterator[StreamChunk],
    request_id: str,
    model_name: str,
    created_at: int,
    stop_sequences: Optional[List[str]] = None,
    has_tools: bool = False,
) -> AsyncIterator[str]:
    """
    Consumes Gemini StreamChunk events and yields standard OpenAI SSE lines.
    Supports structured `tool_calls` extraction when `has_tools` is True.
    """
    # 1. Initial role announcement chunk
    init_chunk = ChatCompletionChunk(
        id=request_id,
        created=created_at,
        model=model_name,
        choices=[
            ChatCompletionChunkChoice(
                index=0,
                delta=ChatCompletionChunkDelta(role="assistant", content=""),
                finish_reason=None,
            )
        ],
    )
    yield format_sse_line(init_chunk)

    accumulated_text = ""
    stop_triggered = False

    try:
        async for chunk in stream_generator:
            if chunk.type == "text" and chunk.delta:
                delta_text = sanitize_surrogate_text(chunk.delta)
                accumulated_text += delta_text

                # Check stop sequences
                if stop_sequences:
                    for stop_seq in stop_sequences:
                        if stop_seq in accumulated_text:
                            idx = accumulated_text.find(stop_seq)
                            valid_text = accumulated_text[:idx]
                            delta_to_yield = valid_text[len(accumulated_text) - len(delta_text):]
                            if delta_to_yield and not has_tools:
                                yield format_sse_line(
                                    ChatCompletionChunk(
                                        id=request_id,
                                        created=created_at,
                                        model=model_name,
                                        choices=[
                                            ChatCompletionChunkChoice(
                                                index=0,
                                                delta=ChatCompletionChunkDelta(content=delta_to_yield),
                                                finish_reason=None,
                                            )
                                        ],
                                    )
                                )
                            accumulated_text = valid_text
                            stop_triggered = True
                            break

                if stop_triggered:
                    break

                # When tools are active, buffer until completion so tool_call fenced blocks can be parsed cleanly
                if not has_tools:
                    yield format_sse_line(
                        ChatCompletionChunk(
                            id=request_id,
                            created=created_at,
                            model=model_name,
                            choices=[
                                ChatCompletionChunkChoice(
                                    index=0,
                                    delta=ChatCompletionChunkDelta(content=delta_text),
                                    finish_reason=None,
                                )
                            ],
                        )
                    )

            elif chunk.type == "done":
                if has_tools and not stop_triggered and chunk.metadata and chunk.metadata.get("full_text"):
                    accumulated_text = str(chunk.metadata["full_text"])
                break

    except Exception as e:
        logger.error("Error during streaming generation: %s", e)
        raise
    finally:
        aclose_fn = getattr(stream_generator, "aclose", None)
        if callable(aclose_fn):
            try:
                await aclose_fn()
            except Exception as err:
                logger.debug("Error closing stream_generator in stream_openai_sse: %s", err)

    finish_reason: str = "stop"
    if has_tools:
        clean_text, tool_calls = OpenAIAdapter.parse_tool_calls(accumulated_text)
        if clean_text:
            yield format_sse_line(
                ChatCompletionChunk(
                    id=request_id,
                    created=created_at,
                    model=model_name,
                    choices=[
                        ChatCompletionChunkChoice(
                            index=0,
                            delta=ChatCompletionChunkDelta(content=clean_text),
                            finish_reason=None,
                        )
                    ],
                )
            )
        if tool_calls:
            indexed_calls = [{**tc, "index": i} for i, tc in enumerate(tool_calls)]
            yield format_sse_line(
                ChatCompletionChunk(
                    id=request_id,
                    created=created_at,
                    model=model_name,
                    choices=[
                        ChatCompletionChunkChoice(
                            index=0,
                            delta=ChatCompletionChunkDelta(tool_calls=indexed_calls),
                            finish_reason=None,
                        )
                    ],
                )
            )
            finish_reason = "tool_calls"

    # 2. Terminal finish_reason chunk
    term_chunk = ChatCompletionChunk(
        id=request_id,
        created=created_at,
        model=model_name,
        choices=[
            ChatCompletionChunkChoice(
                index=0,
                delta=ChatCompletionChunkDelta(),
                finish_reason=finish_reason,  # type: ignore[arg-type]
            )
        ],
    )
    yield format_sse_line(term_chunk)

    # 3. Stream terminator
    yield format_sse_line("[DONE]")


def format_non_streaming_response(
    full_text: str,
    request_id: str,
    model_name: str,
    created_at: int,
    prompt_text: str = "",
    has_tools: bool = False,
) -> ChatCompletionResponse:
    """Formats full buffered text into an OpenAI ChatCompletionResponse object."""
    prompt_tokens = max(1, len(prompt_text) // 4)
    completion_tokens = max(1, len(full_text) // 4)

    clean_text, tool_calls = OpenAIAdapter.parse_tool_calls(full_text) if (has_tools or "```tool_call" in full_text) else (full_text, [])
    if tool_calls:
        msg = ChatCompletionResponseMessage(
            role="assistant",
            content=clean_text if clean_text else None,
            tool_calls=tool_calls,
        )
        finish_reason = "tool_calls"
    else:
        msg = ChatCompletionResponseMessage(role="assistant", content=full_text)
        finish_reason = "stop"

    return ChatCompletionResponse(
        id=request_id,
        created=created_at,
        model=model_name,
        choices=[
            ChatCompletionChoice(
                index=0,
                message=msg,
                finish_reason=finish_reason,  # type: ignore[arg-type]
            )
        ],
        usage=ChatCompletionUsage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
        ),
    )
