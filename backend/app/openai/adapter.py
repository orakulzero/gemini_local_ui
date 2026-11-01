"""
backend/app/openai/adapter.py
Bidirectional translation between OpenAI Chat Completions format and Gemini Web Client payloads.
"""

from __future__ import annotations

import base64
import json
import logging
import mimetypes
import re
import uuid
from typing import Any
import httpx

from backend.app.bridge.models import DEFAULT_MODELS, OPENAI_MODEL_ALIASES
from backend.app.bridge.uploader import GeminiUploader
from backend.app.openai.schemas import (
    ChatCompletionMessageParam,
    ContentPart,
    ImageUrlDetail,
)

logger = logging.getLogger("gemini_openai.adapter")

DATA_URI_PATTERN = re.compile(r"^data:(image\/[a-zA-Z0-9.+_-]+);base64,(.+)$", re.DOTALL)
TOOL_CALL_RE = re.compile(r"```(tool_call|json)?\s*(\{[\s\S]*?\}|\[[\s\S]*?\])\s*```", re.DOTALL)
MAX_IMAGE_BYTES = 25 * 1024 * 1024  # 25 MB


class ModelNotFoundError(Exception):
    """Raised when a requested model ID cannot be resolved."""


class InvalidImagePayloadError(Exception):
    """Raised when an image payload is malformed or exceeds size limits."""


class ImageAttachmentDescriptor:
    def __init__(self, data: bytes, filename: str, mime_type: str) -> None:
        self.data = data
        self.filename = filename
        self.mime_type = mime_type


class OpenAIAdapter:
    """Translates OpenAI requests to Gemini web format and coordinates vision uploads."""

    @staticmethod
    def resolve_model(model_name: str) -> tuple[str, str | None]:
        """
        Resolves model alias or Gem name to (canonical_model_id, gem_id).
        Supports modifier suffixes such as @mode=task or @thinking=4.
        Raises ModelNotFoundError if unresolvable.
        """
        raw_name = model_name.strip()
        clean_name = raw_name
        gem_id: str | None = None
        modifier: str = ""

        if "@" in clean_name:
            base_part, *mod_parts = clean_name.split("@")
            clean_name = base_part.strip()
            kept_mods: list[str] = []
            for mp in mod_parts:
                mp_strip = mp.strip()
                if mp_strip.startswith("gem="):
                    gem_id = mp_strip.split("=", 1)[1].strip()
                elif mp_strip:
                    kept_mods.append(mp_strip)
            modifier = ("@" + "@".join(kept_mods)) if kept_mods else ""

        # Custom Gem targeting (gem:{id}, gem/{id} or gem-{id})
        if clean_name.startswith("gem:"):
            gem_id = clean_name[4:].strip()
            return f"95a4c0abcba24364{modifier}", gem_id
        if clean_name.startswith("gem/"):
            gem_id = clean_name[4:]
            return f"gemini-2.5-pro{modifier}", gem_id
        if clean_name.startswith("gem-") and clean_name not in DEFAULT_MODELS and clean_name not in OPENAI_MODEL_ALIASES:
            gem_id = clean_name[4:]
            return f"gemini-2.5-pro{modifier}", gem_id

        # Alias resolution
        resolved = OPENAI_MODEL_ALIASES.get(clean_name, clean_name)

        if resolved in DEFAULT_MODELS or resolved.startswith("gemini-") or resolved in ("8c46e95b1a07cecc", "56fdd199312815e2", "e6fa609c3fa255c0", "95a4c0abcba24364"):
            return f"{resolved}{modifier}", gem_id

        # Direct check on raw name in aliases or models
        if raw_name in OPENAI_MODEL_ALIASES:
            return OPENAI_MODEL_ALIASES[raw_name], gem_id

        raise ModelNotFoundError(f"The model '{model_name}' does not exist or is not available.")

    @staticmethod
    def build_tools_system_instruction(
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
    ) -> str | None:
        """Builds the `# Tool Use` prompt instruction block for OpenAI Function/Tool Calling."""
        if not tools or tool_choice == "none":
            return None

        tool_specs: list[str] = []
        for tool in tools:
            if not isinstance(tool, dict):
                continue
            fn = tool.get("function", tool)
            if not isinstance(fn, dict):
                continue
            name = fn.get("name", "")
            if not name:
                continue
            desc = fn.get("description", "")
            params = fn.get("parameters", {})
            spec = f"### {name}\n"
            if desc:
                spec += f"{desc}\n"
            if params:
                spec += f"Parameters (JSON Schema): {json.dumps(params, ensure_ascii=False)}\n"
            tool_specs.append(spec)

        if not tool_specs:
            return None

        choice_rule = (
            "If you need to call a tool, output ONLY the tool_call block(s) and nothing else. "
            "If no tool is needed, respond normally."
        )
        if tool_choice == "required":
            choice_rule = (
                "You MUST call at least one of the available tools in your response. "
                "Output ONLY the tool_call block(s) and no other text."
            )
        elif isinstance(tool_choice, dict):
            forced_name = (
                tool_choice.get("function", {}).get("name")
                if isinstance(tool_choice.get("function"), dict)
                else tool_choice.get("name")
            )
            if forced_name:
                choice_rule = (
                    f'You MUST call the tool `{forced_name}`. '
                    "Output ONLY the tool_call block for this tool and no other text."
                )

        return (
            "# Tool Use\n"
            "You have access to the following tools. To call a tool, output a fenced block with the "
            "exact language tag `tool_call` containing a JSON object with `name` and `arguments`:\n\n"
            "```tool_call\n"
            '{"name": "func_name", "arguments": {"arg1": "val1"}}\n'
            "```\n\n"
            f"{choice_rule}\n\n"
            "Available tools:\n\n" + "\n".join(tool_specs)
        )

    @staticmethod
    def parse_tool_calls(text: str) -> tuple[str, list[dict[str, Any]]]:
        """
        Extracts ```tool_call ... ``` blocks from model text and returns
        `(cleaned_text, openai_tool_calls_list)`.
        """
        if not text or "```" not in text:
            return text or "", []

        calls: list[dict[str, Any]] = []
        clean_parts: list[str] = []
        last_idx = 0

        for match in TOOL_CALL_RE.finditer(text):
            fence_tag = (match.group(1) or "").strip().lower()
            raw_block = match.group(2).strip()
            if not raw_block:
                continue
            try:
                parsed = json.loads(raw_block)
            except json.JSONDecodeError:
                continue

            items = parsed if isinstance(parsed, list) else [parsed]
            if not items:
                continue
            if fence_tag == "tool_call":
                is_tool = all(isinstance(it, dict) and bool(it.get("name")) for it in items)
            else:
                is_tool = all(
                    isinstance(it, dict) and bool(it.get("name")) and ("arguments" in it or "input" in it)
                    for it in items
                )

            if not is_tool:
                continue

            clean_parts.append(text[last_idx:match.start()])
            last_idx = match.end()

            for item in items:
                args = item.get("arguments", item.get("input", {}))
                args_str = (
                    args
                    if isinstance(args, str)
                    else json.dumps(args, ensure_ascii=False, separators=(",", ":"))
                )
                call_digest = uuid.uuid4().hex[:12]
                calls.append(
                    {
                        "id": f"call_{call_digest}",
                        "type": "function",
                        "function": {
                            "name": str(item["name"]),
                            "arguments": args_str,
                        },
                    }
                )

        if not calls:
            return text, []

        clean_parts.append(text[last_idx:])
        clean_text = "".join(clean_parts).strip()
        return clean_text, calls

    @classmethod
    def extract_system_instruction(
        cls,
        messages: list[ChatCompletionMessageParam],
        response_format: dict[str, Any] | None = None,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
    ) -> str | None:
        """Extracts and merges all system/developer messages, response format rules, and tool specs."""
        system_parts: list[str] = []
        for msg in messages:
            if msg.role in ("system", "developer"):
                if isinstance(msg.content, str):
                    if msg.content.strip():
                        system_parts.append(msg.content.strip())
                elif isinstance(msg.content, list):
                    for part in msg.content:
                        if isinstance(part, ContentPart) and part.text:
                            system_parts.append(part.text.strip())
                        elif isinstance(part, dict) and part.get("text"):
                            system_parts.append(str(part["text"]).strip())
                        elif isinstance(part, str):
                            system_parts.append(part.strip())

        if response_format and response_format.get("type") == "json_object":
            system_parts.append(
                "IMPORTANT: You must respond with a valid, parseable JSON object only. "
                "Do not wrap your output in markdown code blocks unless explicitly requested."
            )

        tools_inst = cls.build_tools_system_instruction(tools=tools, tool_choice=tool_choice)
        if tools_inst:
            system_parts.append(tools_inst)

        return "\n\n".join(system_parts) if system_parts else None

    @classmethod
    async def parse_image_part(cls, part: ContentPart | dict[str, Any]) -> ImageAttachmentDescriptor:
        """Parses a single image_url part (data URI or remote HTTP URL)."""
        raw_url = ""
        if isinstance(part, ContentPart):
            if isinstance(part.image_url, ImageUrlDetail):
                raw_url = part.image_url.url
            elif isinstance(part.image_url, str):
                raw_url = part.image_url
        elif isinstance(part, dict):
            img_val = part.get("image_url")
            if isinstance(img_val, dict):
                raw_url = img_val.get("url", "")
            elif isinstance(img_val, str):
                raw_url = img_val

        raw_url = raw_url.strip()
        if not raw_url:
            raise InvalidImagePayloadError("image_url part missing 'url' attribute")

        # 1. Base64 Data URI
        match = DATA_URI_PATTERN.match(raw_url)
        if match:
            mime_type = match.group(1).lower()
            b64_data = match.group(2)
            try:
                raw_bytes = base64.b64decode(b64_data)
            except Exception as e:
                raise InvalidImagePayloadError(f"Malformed base64 data URI: {e}")

            if len(raw_bytes) > MAX_IMAGE_BYTES:
                raise InvalidImagePayloadError(
                    f"Image payload ({len(raw_bytes)} bytes) exceeds limit of {MAX_IMAGE_BYTES} bytes"
                )

            ext = mimetypes.guess_extension(mime_type) or ".png"
            filename = f"upload_{len(raw_bytes)}{ext}"
            return ImageAttachmentDescriptor(data=raw_bytes, filename=filename, mime_type=mime_type)

        # 2. Remote HTTP/HTTPS URL
        if raw_url.startswith(("http://", "https://")):
            try:
                async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
                    resp = await client.get(raw_url)
                    if resp.status_code != 200:
                        raise InvalidImagePayloadError(f"Failed fetching image URL (HTTP {resp.status_code})")
                    raw_bytes = resp.content
                    if len(raw_bytes) > MAX_IMAGE_BYTES:
                        raise InvalidImagePayloadError("Fetched image exceeds size limit")

                    content_type = resp.headers.get("content-type", "").split(";")[0].strip().lower()
                    mime_type = content_type if content_type.startswith("image/") else "image/png"
                    ext = mimetypes.guess_extension(mime_type) or ".jpg"
                    filename = f"remote_img{ext}"
                    return ImageAttachmentDescriptor(data=raw_bytes, filename=filename, mime_type=mime_type)
            except Exception as e:
                raise InvalidImagePayloadError(f"Error downloading remote image URL: {e}")

        raise InvalidImagePayloadError("Invalid image_url format: must be base64 data URI or HTTP(S) URL")

    @classmethod
    async def extract_turns_and_attachments(
        cls,
        messages: list[ChatCompletionMessageParam],
        system_instruction: str | None = None,
    ) -> tuple[str, list[dict[str, str]], list[ImageAttachmentDescriptor]]:
        """
        Parses non-system messages, coalesces consecutive identical roles,
        extracts unuploaded image descriptors, and returns (final_prompt, history, image_descriptors).
        """
        raw_turns: list[tuple[str, str, list[ImageAttachmentDescriptor]]] = []

        for msg in messages:
            if msg.role in ("system", "developer"):
                continue

            text_parts: list[str] = []
            img_descriptors: list[ImageAttachmentDescriptor] = []

            if isinstance(msg.content, str):
                if msg.content:
                    text_parts.append(msg.content)
            elif isinstance(msg.content, list):
                for part in msg.content:
                    if isinstance(part, str):
                        text_parts.append(part)
                    elif isinstance(part, ContentPart):
                        if part.type == "text" and part.text:
                            text_parts.append(part.text)
                        elif part.type == "image_url":
                            desc = await cls.parse_image_part(part)
                            img_descriptors.append(desc)
                    elif isinstance(part, dict):
                        p_type = part.get("type", "text")
                        if p_type == "text" and part.get("text"):
                            text_parts.append(str(part["text"]))
                        elif p_type == "image_url":
                            desc = await cls.parse_image_part(part)
                            img_descriptors.append(desc)

            turn_text = "\n\n".join(text_parts).strip()
            eff_role = msg.role
            if msg.role in ("tool", "function"):
                tool_label = msg.name or msg.tool_call_id or "tool"
                turn_text = f"[Tool result for {tool_label}]: {turn_text}"
                eff_role = "user"
            elif msg.role == "assistant" and msg.tool_calls:
                tc_lines: list[str] = []
                for tc in msg.tool_calls:
                    if isinstance(tc, dict):
                        fn = tc.get("function", {})
                        fn_name = fn.get("name", "") if isinstance(fn, dict) else ""
                        fn_args = fn.get("arguments", "{}") if isinstance(fn, dict) else "{}"
                        if fn_name:
                            tc_lines.append(f"[Assistant called tool {fn_name}({fn_args})]")
                if tc_lines:
                    tc_summary = "\n".join(tc_lines)
                    turn_text = f"{turn_text}\n{tc_summary}".strip() if turn_text else tc_summary

            raw_turns.append((eff_role, turn_text, img_descriptors))

        if not raw_turns:
            return "", [], []

        # Coalesce consecutive turns with the same role
        coalesced: list[tuple[str, str, list[ImageAttachmentDescriptor]]] = []
        for role, text, imgs in raw_turns:
            if coalesced and coalesced[-1][0] == role:
                prev_role, prev_text, prev_imgs = coalesced[-1]
                merged_text = f"{prev_text}\n\n{text}".strip() if prev_text and text else (prev_text or text)
                coalesced[-1] = (prev_role, merged_text, prev_imgs + imgs)
            else:
                coalesced.append((role, text, imgs))

        # Separate latest user turn from preceding history
        final_turn_role, final_prompt, final_images = coalesced[-1]

        history: list[dict[str, str]] = []
        for r, t, _ in coalesced[:-1]:
            history.append({"role": "model" if r == "assistant" else "user", "content": t})

        # Inject system instruction into prompt context
        if system_instruction:
            final_prompt = f"[System Instruction]\n{system_instruction}\n\n[User Prompt]\n{final_prompt}"

        return final_prompt, history, final_images

    @classmethod
    async def stage_and_upload_images(
        cls,
        descriptors: list[ImageAttachmentDescriptor],
        uploader: GeminiUploader,
    ) -> list[dict[str, str]]:
        """Uploads parsed images via GeminiUploader and returns attachment dicts."""
        attachments: list[dict[str, str]] = []
        for desc in descriptors:
            uri = await uploader.upload_file(
                data=desc.data,
                filename=desc.filename,
                mime_type=desc.mime_type,
            )
            attachments.append({"uri": uri, "filename": desc.filename})
        return attachments
