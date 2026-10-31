"""
backend/app/anthropic/sse.py
Anthropic Server-Sent Events (SSE) streaming generator.
Emits official Anthropic streaming event sequences:
message_start -> content_block_start / content_block_delta / content_block_stop -> message_delta -> message_stop.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from typing import Any
import uuid

from backend.app.anthropic.adapter import AnthropicAdapter
from backend.app.bridge.streaming_parser import StreamChunk, sanitize_surrogate_text

logger = logging.getLogger("gemini_anthropic.sse")


def generate_message_id() -> str:
    """Generates an Anthropic-standard message ID (msg_...)."""
    return f"msg_{uuid.uuid4().hex[:24]}"


def format_anthropic_event(event: str, data: dict[str, Any]) -> str:
    """Formats event name and JSON data into Anthropic SSE format."""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, separators=(',', ':'))}\n\n"


async def stream_anthropic_sse(
    stream_generator: AsyncIterator[StreamChunk],
    request_id: str,
    model_name: str,
    input_tokens: int = 0,
    stop_sequences: list[str] | None = None,
    has_tools: bool = False,
    thinking_enabled: bool = True,
) -> AsyncIterator[str]:
    """
    Consumes Gemini StreamChunk objects and emits official Anthropic SSE events.
    Supports thinking_delta, text_delta, and input_json_delta for tool_use.
    """
    # 1. Emit message_start
    yield format_anthropic_event(
        "message_start",
        {
            "type": "message_start",
            "message": {
                "id": request_id,
                "type": "message",
                "role": "assistant",
                "model": model_name,
                "content": [],
                "stop_reason": None,
                "stop_sequence": None,
                "usage": {
                    "input_tokens": max(1, input_tokens),
                    "output_tokens": 1,
                },
            },
        },
    )

    block_index = 0
    thought_block_open = False
    text_block_open = False
    accumulated_thoughts: list[str] = []
    accumulated_text_chunks: list[str] = []
    accumulated_full_text = ""
    stop_reason: str = "end_turn"
    stop_sequence_matched: str | None = None
    stop_triggered = False

    try:
        async for chunk in stream_generator:
            # Handle Thinking / Reasoning Chunks
            if chunk.type == "thought" and chunk.delta and thinking_enabled:
                if text_block_open:
                    yield format_anthropic_event(
                        "content_block_stop",
                        {"type": "content_block_stop", "index": block_index},
                    )
                    block_index += 1
                    text_block_open = False

                if not thought_block_open:
                    yield format_anthropic_event(
                        "content_block_start",
                        {
                            "type": "content_block_start",
                            "index": block_index,
                            "content_block": {"type": "thinking", "thinking": ""},
                        },
                    )
                    thought_block_open = True

                delta_thought = sanitize_surrogate_text(chunk.delta)
                accumulated_thoughts.append(delta_thought)
                yield format_anthropic_event(
                    "content_block_delta",
                    {
                        "type": "content_block_delta",
                        "index": block_index,
                        "delta": {"type": "thinking_delta", "thinking": delta_thought},
                    },
                )

            # Handle Text Chunks
            elif chunk.type == "text" and chunk.delta:
                # Close thinking block if it was active
                if thought_block_open:
                    yield format_anthropic_event(
                        "content_block_stop",
                        {"type": "content_block_stop", "index": block_index},
                    )
                    block_index += 1
                    thought_block_open = False

                delta_text = sanitize_surrogate_text(chunk.delta)
                accumulated_text_chunks.append(delta_text)
                accumulated_full_text += delta_text

                # Check stop sequences
                if stop_sequences:
                    for stop_seq in stop_sequences:
                        if stop_seq in accumulated_full_text:
                            idx = accumulated_full_text.find(stop_seq)
                            accumulated_full_text = accumulated_full_text[:idx]
                            stop_sequence_matched = stop_seq
                            stop_reason = "stop_sequence"
                            stop_triggered = True
                            break
                    if stop_triggered:
                        break

                # If tools are not active, stream text in real-time
                if not has_tools:
                    if not text_block_open:
                        yield format_anthropic_event(
                            "content_block_start",
                            {
                                "type": "content_block_start",
                                "index": block_index,
                                "content_block": {"type": "text", "text": ""},
                            },
                        )
                        text_block_open = True

                    yield format_anthropic_event(
                        "content_block_delta",
                        {
                            "type": "content_block_delta",
                            "index": block_index,
                            "delta": {"type": "text_delta", "text": delta_text},
                        },
                    )

            elif chunk.type == "done":
                if has_tools and not stop_triggered and chunk.metadata and chunk.metadata.get("full_text"):
                    accumulated_full_text = str(chunk.metadata["full_text"])
                break

    except Exception as e:
        logger.error("Error during Anthropic streaming generation: %s", e)
        yield format_anthropic_event(
            "error",
            {"type": "error", "error": {"type": "api_error", "message": str(e)}},
        )
        raise
    finally:
        aclose_fn = getattr(stream_generator, "aclose", None)
        if callable(aclose_fn):
            try:
                await aclose_fn()
            except Exception as err:
                logger.debug("Error closing stream_generator in stream_anthropic_sse: %s", err)

    # Close any still-open thinking block
    if thought_block_open:
        yield format_anthropic_event(
            "content_block_stop",
            {"type": "content_block_stop", "index": block_index},
        )
        block_index += 1
        thought_block_open = False

    # When tools are enabled, process buffered text and emit tool_use blocks
    if has_tools:
        clean_text, tool_calls = AnthropicAdapter.parse_tool_calls(accumulated_full_text)
        if clean_text:
            yield format_anthropic_event(
                "content_block_start",
                {
                    "type": "content_block_start",
                    "index": block_index,
                    "content_block": {"type": "text", "text": ""},
                },
            )
            yield format_anthropic_event(
                "content_block_delta",
                {
                    "type": "content_block_delta",
                    "index": block_index,
                    "delta": {"type": "text_delta", "text": clean_text},
                },
            )
            yield format_anthropic_event(
                "content_block_stop",
                {"type": "content_block_stop", "index": block_index},
            )
            block_index += 1

        if tool_calls:
            for tc in tool_calls:
                yield format_anthropic_event(
                    "content_block_start",
                    {
                        "type": "content_block_start",
                        "index": block_index,
                        "content_block": {
                            "type": "tool_use",
                            "id": tc.id,
                            "name": tc.name,
                            "input": {},
                        },
                    },
                )
                input_json = json.dumps(tc.input, ensure_ascii=False)
                yield format_anthropic_event(
                    "content_block_delta",
                    {
                        "type": "content_block_delta",
                        "index": block_index,
                        "delta": {"type": "input_json_delta", "partial_json": input_json},
                    },
                )
                yield format_anthropic_event(
                    "content_block_stop",
                    {"type": "content_block_stop", "index": block_index},
                )
                block_index += 1
            if not stop_triggered:
                stop_reason = "tool_use"
        elif not clean_text and accumulated_full_text:
            yield format_anthropic_event(
                "content_block_start",
                {
                    "type": "content_block_start",
                    "index": block_index,
                    "content_block": {"type": "text", "text": ""},
                },
            )
            yield format_anthropic_event(
                "content_block_delta",
                {
                    "type": "content_block_delta",
                    "index": block_index,
                    "delta": {"type": "text_delta", "text": accumulated_full_text},
                },
            )
            yield format_anthropic_event(
                "content_block_stop",
                {"type": "content_block_stop", "index": block_index},
            )
            block_index += 1
    else:
        # Close open text block if real-time streaming was active
        if text_block_open:
            yield format_anthropic_event(
                "content_block_stop",
                {"type": "content_block_stop", "index": block_index},
            )
            block_index += 1
            text_block_open = False

    output_tokens = max(1, (len(accumulated_full_text) + sum(len(t) for t in accumulated_thoughts)) // 3)

    # 3. Emit message_delta
    yield format_anthropic_event(
        "message_delta",
        {
            "type": "message_delta",
            "delta": {
                "stop_reason": stop_reason,
                "stop_sequence": stop_sequence_matched,
            },
            "usage": {
                "output_tokens": output_tokens,
            },
        },
    )

    # 4. Emit message_stop
    yield format_anthropic_event("message_stop", {"type": "message_stop"})
