# tests/e2e/harness/openai_harness.py
"""OpenAI-compatible API adapter, model registry, SSE encoder, and vision parser."""

from __future__ import annotations
import json
import time
import uuid
import re
from typing import Any, Dict, List, Optional, Tuple

class OpenAIModelRegistry:
    """Manages models and aliases matching the OpenAI API specification."""

    AVAILABLE_MODELS = [
        {"id": "gemini-2.5-flash", "owned_by": "google-gemini"},
        {"id": "gemini-2.5-pro", "owned_by": "google-gemini"},
        {"id": "gemini-2.0-flash", "owned_by": "google-gemini"},
        {"id": "gemini-2.0-flash-thinking", "owned_by": "google-gemini"},
        {"id": "gpt-4o", "owned_by": "google-gemini-alias", "target": "gemini-2.5-pro"},
        {"id": "gpt-4-turbo", "owned_by": "google-gemini-alias", "target": "gemini-2.5-pro"},
        {"id": "gpt-3.5-turbo", "owned_by": "google-gemini-alias", "target": "gemini-2.0-flash"},
    ]

    ALIAS_MAP = {
        "gpt-4o": "gemini-2.5-pro",
        "gpt-4-turbo": "gemini-2.5-pro",
        "gpt-3.5-turbo": "gemini-2.0-flash",
    }

    @classmethod
    def get_models_response(cls) -> Dict[str, Any]:
        return {
            "object": "list",
            "data": [
                {
                    "id": m["id"],
                    "object": "model",
                    "created": 1700000000,
                    "owned_by": m["owned_by"],
                    "permission": [],
                }
                for m in cls.AVAILABLE_MODELS
            ],
        }

    @classmethod
    def resolve_model(cls, model_id: str) -> str:
        return cls.ALIAS_MAP.get(model_id, model_id)


class OpenAIChatAdapter:
    """Adapts OpenAI Chat Completions requests to Gemini format and vice-versa."""

    @classmethod
    def parse_request(cls, req: Dict[str, Any]) -> Dict[str, Any]:
        model = req.get("model", "gemini-2.0-flash")
        resolved_model = OpenAIModelRegistry.resolve_model(model)
        messages = req.get("messages", [])
        stream = bool(req.get("stream", False))
        temperature = req.get("temperature", 0.7)
        top_p = req.get("top_p", 0.95)
        max_tokens = req.get("max_tokens") or req.get("max_completion_tokens")
        stop = req.get("stop")

        system_prompts: List[str] = []
        gemini_turns: List[Dict[str, Any]] = []

        for m in messages:
            role = m.get("role", "user")
            content = m.get("content", "")
            if role in ("system", "developer"):
                if isinstance(content, str):
                    system_prompts.append(content)
            elif role in ("user", "assistant"):
                target_role = "user" if role == "user" else "model"
                gemini_turns.append({"role": target_role, "content": content})

        system_instruction = "\n\n".join(system_prompts) if system_prompts else None

        return {
            "model": resolved_model,
            "raw_model": model,
            "system_instruction": system_instruction,
            "turns": gemini_turns,
            "stream": stream,
            "temperature": temperature,
            "top_p": top_p,
            "max_tokens": max_tokens,
            "stop": stop,
        }

    @classmethod
    def format_non_stream_response(
        cls,
        completion_text: str,
        model: str,
        prompt_tokens: int = 10,
        completion_tokens: int = 50,
    ) -> Dict[str, Any]:
        cmpl_id = f"chatcmpl-{uuid.uuid4().hex[:16]}"
        now = int(time.time())
        return {
            "id": cmpl_id,
            "object": "chat.completion",
            "created": now,
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": completion_text,
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
            },
        }


class OpenAISSEChunkEncoder:
    """Encodes streaming Server-Sent Events chunks matching OpenAI specification."""

    @classmethod
    def encode_role_chunk(cls, stream_id: str, model: str) -> str:
        payload = {
            "id": stream_id,
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": model,
            "choices": [{"index": 0, "delta": {"role": "assistant", "content": ""}, "finish_reason": None}],
        }
        return f"data: {json.dumps(payload, separators=(',', ':'))}\n\n"

    @classmethod
    def encode_content_chunk(cls, stream_id: str, model: str, delta_text: str) -> str:
        payload = {
            "id": stream_id,
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": model,
            "choices": [{"index": 0, "delta": {"content": delta_text}, "finish_reason": None}],
        }
        return f"data: {json.dumps(payload, separators=(',', ':'))}\n\n"

    @classmethod
    def encode_finish_chunk(cls, stream_id: str, model: str, reason: str = "stop") -> str:
        payload = {
            "id": stream_id,
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": model,
            "choices": [{"index": 0, "delta": {}, "finish_reason": reason}],
        }
        return f"data: {json.dumps(payload, separators=(',', ':'))}\n\n"

    @classmethod
    def encode_done(cls) -> str:
        return "data: [DONE]\n\n"


class OpenAIMultimodalParser:
    """Parses OpenAI image_url vision payloads."""

    DATA_URI_PATTERN = re.compile(r"^data:(image\/[a-zA-Z0-9.+]+);base64,(.+)$")

    @classmethod
    def parse_image_part(cls, part: Dict[str, Any]) -> Tuple[bool, Optional[str], Optional[str]]:
        """Returns (is_valid, mime_type, base64_payload_or_url)."""
        if part.get("type") != "image_url":
            return False, None, None
        img_info = part.get("image_url", {})
        url = img_info.get("url", "")
        if not url:
            return False, None, None

        match = cls.DATA_URI_PATTERN.match(url)
        if match:
            mime_type = match.group(1)
            b64_data = match.group(2)
            return True, mime_type, b64_data

        if url.startswith("http://") or url.startswith("https://"):
            return True, "remote_url", url

        return False, None, None
