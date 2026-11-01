"""
backend/app/bridge/models.py
Gemini Web model definitions, capabilities, OpenAI alias resolution,
Custom Gems serializer, and HTTP header generator.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import hashlib
import json
import re
import time
from typing import Any
import uuid


class ModelTier(str, Enum):
    FREE = "free"
    PRO = "pro"
    ULTRA = "ultra"


@dataclass(frozen=True)
class ModelCapabilities:
    supports_thinking: bool = False
    supports_vision: bool = True
    supports_file_upload: bool = True
    supports_code_execution: bool = True
    context_window_tokens: int = 1_000_000
    max_output_tokens: int = 8_192


@dataclass(frozen=True)
class ModelInfo:
    id: str
    display_name: str
    description: str = ""
    model_number: int = 1
    thinking_flag: int = 1          # 1: Low (Standard), 4: Medium (Medium), 2: High (Extended)
    capacity_tail: int = 12         # 12: Standard, 15: Pro tier
    tier: ModelTier = ModelTier.FREE
    capabilities: ModelCapabilities = field(default_factory=ModelCapabilities)
    is_default: bool = False
    mode_tag: str | None = None  # e.g. "Argon4p0" for 4 Argon
    feature_mode: int = 0           # 0: Chat, 40: Task (Spark/Remy Task mode)

    @property
    def supports_thinking(self) -> bool:
        return self.capabilities.supports_thinking or self.thinking_flag in (2, 3, 4)


DEFAULT_MODELS: dict[str, ModelInfo] = {
    "8c46e95b1a07cecc": ModelInfo(
        id="8c46e95b1a07cecc",
        display_name="3.5 Flash-Lite",
        description="Fastest responses",
        model_number=6,
        thinking_flag=1,
        capacity_tail=12,
        tier=ModelTier.FREE,
        capabilities=ModelCapabilities(
            supports_thinking=True,
            supports_vision=True,
            supports_file_upload=True,
            supports_code_execution=True,
            context_window_tokens=1_000_000,
            max_output_tokens=8_192,
        ),
    ),
    "56fdd199312815e2": ModelInfo(
        id="56fdd199312815e2",
        display_name="3.8 Flash",
        description="All-round help",
        model_number=1,
        thinking_flag=1,
        capacity_tail=12,
        tier=ModelTier.FREE,
        capabilities=ModelCapabilities(
            supports_thinking=True,
            supports_vision=True,
            supports_file_upload=True,
            supports_code_execution=True,
            context_window_tokens=1_000_000,
            max_output_tokens=8_192,
        ),
        is_default=True,
    ),
    "e6fa609c3fa255c0": ModelInfo(
        id="e6fa609c3fa255c0",
        display_name="3.1 Pro",
        description="Advanced reasoning capabilities",
        model_number=3,
        thinking_flag=2,
        capacity_tail=15,
        tier=ModelTier.PRO,
        capabilities=ModelCapabilities(
            supports_thinking=True,
            supports_vision=True,
            supports_file_upload=True,
            supports_code_execution=True,
            context_window_tokens=2_000_000,
            max_output_tokens=16_384,
        ),
    ),
    "95a4c0abcba24364": ModelInfo(
        id="95a4c0abcba24364",
        display_name="4 Argon",
        description="Complex tasks · New",
        model_number=8,
        thinking_flag=2,
        capacity_tail=15,
        tier=ModelTier.PRO,
        capabilities=ModelCapabilities(
            supports_thinking=True,
            supports_vision=True,
            supports_file_upload=True,
            supports_code_execution=True,
            context_window_tokens=2_000_000,
            max_output_tokens=16_384,
        ),
        mode_tag="Argon4p0",
    ),
    "cf41b0e0dd7d53e5": ModelInfo(
        id="cf41b0e0dd7d53e5",
        display_name="Auto (Gemini 3 Models)",
        description="Automatic Gemini 3 model selection",
        model_number=1,
        thinking_flag=1,
        capacity_tail=12,
        tier=ModelTier.FREE,
        capabilities=ModelCapabilities(
            supports_thinking=True,
            supports_vision=True,
            supports_file_upload=True,
            supports_code_execution=True,
            context_window_tokens=1_000_000,
            max_output_tokens=8_192,
        ),
    ),
    "gemini-2.5-flash": ModelInfo(
        id="gemini-2.5-flash",
        display_name="Gemini 2.5 Flash",
        description="Fast, multimodal model for high-frequency tasks",
        model_number=1,
        thinking_flag=1,
        capacity_tail=12,
        tier=ModelTier.FREE,
        capabilities=ModelCapabilities(
            supports_thinking=False,
            supports_vision=True,
            supports_file_upload=True,
            supports_code_execution=True,
            context_window_tokens=1_000_000,
            max_output_tokens=8_192,
        ),
    ),
    "gemini-2.5-pro": ModelInfo(
        id="gemini-2.5-pro",
        display_name="Gemini 2.5 Pro",
        description="Advanced reasoning and complex problem solving",
        model_number=2,
        thinking_flag=1,
        capacity_tail=15,
        tier=ModelTier.PRO,
        capabilities=ModelCapabilities(
            supports_thinking=False,
            supports_vision=True,
            supports_file_upload=True,
            supports_code_execution=True,
            context_window_tokens=2_000_000,
            max_output_tokens=8_192,
        ),
    ),
    "gemini-2.0-flash-thinking": ModelInfo(
        id="gemini-2.0-flash-thinking",
        display_name="Gemini 2.0 Flash Thinking",
        description="Model with explicit reasoning and thought chain",
        model_number=1,
        thinking_flag=2,
        capacity_tail=12,
        tier=ModelTier.FREE,
        capabilities=ModelCapabilities(
            supports_thinking=True,
            supports_vision=True,
            supports_file_upload=True,
            supports_code_execution=True,
            context_window_tokens=1_000_000,
            max_output_tokens=8_192,
        ),
    ),
    "gemini-2.0-flash": ModelInfo(
        id="gemini-2.0-flash",
        display_name="Gemini 2.0 Flash",
        description="Next generation fast multimodal model",
        model_number=1,
        thinking_flag=1,
        capacity_tail=12,
        tier=ModelTier.FREE,
        capabilities=ModelCapabilities(
            supports_thinking=False,
            supports_vision=True,
            supports_file_upload=True,
            supports_code_execution=True,
            context_window_tokens=1_000_000,
            max_output_tokens=8_192,
        ),
    ),
    "gemini-ultra": ModelInfo(
        id="gemini-ultra",
        display_name="Gemini Ultra",
        description="Maximum capability enterprise model",
        model_number=3,
        thinking_flag=1,
        capacity_tail=15,
        tier=ModelTier.ULTRA,
        capabilities=ModelCapabilities(
            supports_thinking=False,
            supports_vision=True,
            supports_file_upload=True,
            supports_code_execution=True,
            context_window_tokens=2_000_000,
            max_output_tokens=8_192,
        ),
    ),
    "gemini-4-argon-deep-think": ModelInfo(
        id="95a4c0abcba24364",
        display_name="4 Argon Deep Think",
        description="Parallel Tree-of-Thought Deep Think reasoning (Ultra)",
        model_number=1,
        thinking_flag=3,
        capacity_tail=15,
        tier=ModelTier.ULTRA,
        capabilities=ModelCapabilities(
            supports_thinking=True,
            supports_vision=True,
            supports_file_upload=True,
            supports_code_execution=True,
            context_window_tokens=2_000_000,
            max_output_tokens=16_384,
        ),
        mode_tag="Argon4p0",
    ),
}

UI_PRIMARY_MODEL_IDS: list[str] = [
    "8c46e95b1a07cecc",
    "56fdd199312815e2",
    "e6fa609c3fa255c0",
    "95a4c0abcba24364",
]

OPENAI_MODEL_ALIASES: dict[str, str] = {
    "gpt-4o": "gemini-2.5-pro",
    "gpt-4-turbo": "gemini-2.5-pro",
    "gpt-4": "gemini-2.5-pro",
    "gpt-3.5-turbo": "gemini-2.5-flash",
    "o1": "gemini-2.0-flash-thinking",
    "o3-mini": "gemini-2.0-flash-thinking",
    "gemini-flash": "gemini-2.5-flash",
    "gemini-pro": "gemini-2.5-pro",
    "gemini-3.5-flash-lite": "8c46e95b1a07cecc",
    "gemini-3.8-flash": "56fdd199312815e2",
    "gemini-3.1-pro": "e6fa609c3fa255c0",
    "gemini-4-argon": "95a4c0abcba24364",
    "3.5-flash-lite": "8c46e95b1a07cecc",
    "3.5 flash-lite": "8c46e95b1a07cecc",
    "3.5 Flash-Lite": "8c46e95b1a07cecc",
    "3.8-flash": "56fdd199312815e2",
    "3.8 flash": "56fdd199312815e2",
    "3.8 Flash": "56fdd199312815e2",
    "3.1-pro": "e6fa609c3fa255c0",
    "3.1 pro": "e6fa609c3fa255c0",
    "3.1 Pro": "e6fa609c3fa255c0",
    "4-argon": "95a4c0abcba24364",
    "4 argon": "95a4c0abcba24364",
    "4 Argon": "95a4c0abcba24364",
    "argon": "95a4c0abcba24364",
    "task": "95a4c0abcba24364@mode=task",
    "tasks": "95a4c0abcba24364@mode=task",
    "gemini-task": "95a4c0abcba24364@mode=task",
    "gemini-4-task": "95a4c0abcba24364@mode=task",
    "gemini-task-mode": "95a4c0abcba24364@mode=task",
    "gemini-4-argon-deep-think": "gemini-4-argon-deep-think",
    "deep-think": "gemini-4-argon-deep-think",
    "deep_think": "gemini-4-argon-deep-think",
}


def resolve_model(model_name: str) -> ModelInfo:
    """Resolves an alias or canonical model string into a ModelInfo instance."""
    clean_name = model_name.strip()
    override_thinking: int | None = None
    override_feature_mode: int = 0
    force_deep_think: bool = False

    if "@" in clean_name:
        parts = clean_name.split("@")
        clean_name = parts[0].strip()
        for part in parts[1:]:
            p = part.strip()
            if p.startswith("thinking="):
                val = p.split("=", 1)[1].strip()
                if val.isdigit():
                    override_thinking = int(val)
            elif p.startswith("mode="):
                val = p.split("=", 1)[1].strip().lower()
                if val in ("task", "tasks", "40", "spark"):
                    override_feature_mode = 40
                elif val in ("deep-think", "deep_think", "deepthink"):
                    override_thinking = 3
                    force_deep_think = True

    target_id = OPENAI_MODEL_ALIASES.get(clean_name, clean_name)
    if target_id in DEFAULT_MODELS:
        base_info = DEFAULT_MODELS[target_id]
        eff_thinking = override_thinking if override_thinking is not None else base_info.thinking_flag
        ctx_tokens = (
            max(base_info.capabilities.context_window_tokens, 2_000_000)
            if (force_deep_think or eff_thinking == 3)
            else base_info.capabilities.context_window_tokens
        )
        if (
            eff_thinking != base_info.thinking_flag
            or override_feature_mode != base_info.feature_mode
            or ctx_tokens != base_info.capabilities.context_window_tokens
        ):
            return ModelInfo(
                id=base_info.id,
                display_name=base_info.display_name,
                description=base_info.description,
                model_number=base_info.model_number,
                thinking_flag=eff_thinking,
                capacity_tail=base_info.capacity_tail,
                tier=base_info.tier,
                capabilities=ModelCapabilities(
                    supports_thinking=base_info.capabilities.supports_thinking or eff_thinking in (2, 3, 4),
                    supports_vision=base_info.capabilities.supports_vision,
                    supports_file_upload=base_info.capabilities.supports_file_upload,
                    supports_code_execution=base_info.capabilities.supports_code_execution,
                    context_window_tokens=ctx_tokens,
                    max_output_tokens=base_info.capabilities.max_output_tokens,
                ),
                is_default=base_info.is_default,
                mode_tag=base_info.mode_tag,
                feature_mode=override_feature_mode,
            )
        return base_info

    # Dynamic fallback based on naming heuristics
    is_argon = "argon" in target_id.lower() or target_id == "95a4c0abcba24364"
    is_thinking = "thinking" in target_id.lower() or "pro" in target_id.lower() or is_argon
    is_pro = "pro" in target_id.lower() or "ultra" in target_id.lower() or is_argon
    model_number = 8 if is_argon else (3 if is_pro else 1)
    thinking_flag = override_thinking if override_thinking is not None else (2 if is_thinking else 1)
    capacity_tail = 15 if is_pro else 12

    return ModelInfo(
        id=target_id,
        display_name=target_id,
        model_number=model_number,
        thinking_flag=thinking_flag,
        capacity_tail=capacity_tail,
        capabilities=ModelCapabilities(
            supports_thinking=is_thinking or thinking_flag in (2, 3, 4),
            context_window_tokens=2_000_000 if (is_pro or thinking_flag == 3) else 1_000_000,
        ),
        mode_tag="Argon4p0" if is_argon else None,
        feature_mode=override_feature_mode,
    )


@dataclass
class Gem:
    id: str
    name: str
    description: str
    prompt: str
    icon: str | None = None
    is_builtin: bool = False
    created_at: int | None = None
    updated_at: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "title": self.name,
            "description": self.description,
            "prompt": self.prompt,
            "system_prompt": self.prompt,
            "icon": self.icon,
            "is_builtin": self.is_builtin,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class GemSerializer:
    """Serializes and deserializes Custom Gems payloads for Batchexecute RPCs."""

    @staticmethod
    def build_list_payload(is_custom: bool = True, language: str = "en") -> list[Any]:
        """Builds payload for CNgdBe (2 for custom, 3 for system gems)."""
        mode = 2 if is_custom else 3
        return [mode, [language], 0]

    @staticmethod
    def parse_list_response(response_data: Any, is_builtin: bool = False) -> list[Gem]:
        """Parses decoded response from CNgdBe into a list of Gem objects."""
        gems: list[Gem] = []
        if not response_data or not isinstance(response_data, list):
            return gems

        # Resolve candidate gem_list from container envelopes
        gem_list = response_data
        if len(response_data) > 0 and isinstance(response_data[0], list):
            gem_list = response_data[0]
        elif len(response_data) > 1 and isinstance(response_data[1], list):
            gem_list = response_data[1]
        elif len(response_data) > 2 and isinstance(response_data[2], list):
            gem_list = response_data[2]

        for item in gem_list:
            if not isinstance(item, list) or len(item) < 2:
                continue

            gem_id = str(item[0])
            name = ""
            description = ""
            prompt = ""
            icon = None
            created_at = None

            # Handle nested or flat formats
            if len(item) > 1:
                if isinstance(item[1], list):
                    if len(item[1]) > 0 and item[1][0]:
                        name = str(item[1][0])
                    if len(item[1]) > 1 and item[1][1]:
                        description = str(item[1][1])
                elif isinstance(item[1], str):
                    name = item[1]

            if len(item) > 2:
                if isinstance(item[2], list):
                    if len(item[2]) > 0 and item[2][0]:
                        prompt = str(item[2][0])
                elif isinstance(item[2], str):
                    if not description:
                        description = item[2]
                    elif not prompt:
                        prompt = item[2]

            if len(item) > 3:
                if isinstance(item[3], str) and not prompt:
                    prompt = item[3]
                elif isinstance(item[3], (int, float)):
                    created_at = int(item[3])

            if len(item) > 4 and isinstance(item[4], str) and not icon:
                icon = item[4]

            if len(item) > 5 and isinstance(item[5], str) and not icon:
                icon = item[5]

            gems.append(
                Gem(
                    id=gem_id,
                    name=name,
                    description=description,
                    prompt=prompt,
                    icon=icon,
                    is_builtin=is_builtin,
                    created_at=created_at,
                )
            )

        return gems

    @staticmethod
    def build_create_payload(name: str, description: str, prompt: str) -> list[Any]:
        """Builds payload for oMH3Zd."""
        inner = [name, description, prompt, None, None, None, None, None, 0, None, 1, None, None, None, []]
        return [inner]

    @staticmethod
    def parse_create_response(response_data: Any) -> str:
        """Extracts newly minted gem_id from oMH3Zd response."""
        if isinstance(response_data, str) and response_data.strip():
            return response_data.strip()
        if isinstance(response_data, list) and len(response_data) > 0:
            if isinstance(response_data[0], str) and response_data[0].strip():
                return response_data[0].strip()
            if isinstance(response_data[0], list) and len(response_data[0]) > 0 and isinstance(response_data[0][0], str):
                return response_data[0][0].strip()
        if response_data is None:
            return f"gem_{uuid.uuid4().hex[:12]}"
        return str(response_data)

    @staticmethod
    def build_update_payload(gem_id: str, name: str, description: str, prompt: str) -> list[Any]:
        """Builds payload for kHv0Vd."""
        inner = [name, description, prompt, None, None, None, None, None, 0, None, 1, None, None, None, [], 0]
        return [gem_id, inner]

    @staticmethod
    def build_delete_payload(gem_id: str) -> list[Any]:
        """Builds payload for UXcSJb."""
        return [gem_id]


class HeaderBuilder:
    """Builds synthetic Google Gemini browser headers matching genuine Chrome traces."""

    DEFAULT_USER_AGENT = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/133.0.0.0 Safari/537.36"
    )
    DEFAULT_SEC_CH_UA = '"Not(A:Brand";v="99", "Google Chrome";v="133", "Chromium";v="133"'
    DEFAULT_SEC_CH_UA_PLATFORM = '"Windows"'

    @classmethod
    def format_cookies(cls, cookies: dict[str, str]) -> str:
        """Formats dictionary into an HTTP Cookie header string."""
        return "; ".join(f"{k}={v}" for k, v in cookies.items() if v)

    @classmethod
    def extract_sapisid(cls, cookies: dict[str, str]) -> str | None:
        """Extracts SAPISID (or __Secure-1PAPISID / __Secure-3PAPISID) from cookies dict."""
        if not cookies or not isinstance(cookies, dict):
            return None
        for key in ("SAPISID", "__Secure-1PAPISID", "__Secure-3PAPISID"):
            val = cookies.get(key)
            if val and isinstance(val, str) and val.strip():
                return val.strip()
        return None

    @classmethod
    def make_sapisidhash(
        cls,
        sapisid: str,
        origin: str = "https://gemini.google.com",
        timestamp: int | None = None,
    ) -> str:
        """Computes Google Authorization SAPISIDHASH header (`SAPISIDHASH {ts}_{sha1}`)."""
        ts = int(timestamp if timestamp is not None else time.time())
        digest = hashlib.sha1(f"{ts} {sapisid.strip()} {origin}".encode()).hexdigest()
        return f"SAPISIDHASH {ts}_{digest}"

    @classmethod
    def build_compact_model_jspb_header(cls, model: ModelInfo | str) -> str:
        """
        Synthesizes the compact 9-element x-goog-ext-525001261-jspb header
        `[1,null,null,null,"<model_hash>",null,null,0,[4]]` that forces picker model routing.
        """
        info = resolve_model(model) if isinstance(model, str) else model
        model_hash = info.id
        if model_hash not in DEFAULT_MODELS:
            mode_hashes = {
                1: "56fdd199312815e2",
                3: "e6fa609c3fa255c0",
                6: "8c46e95b1a07cecc",
                8: "95a4c0abcba24364",
            }
            model_hash = mode_hashes.get(info.model_number, info.id)
        return json.dumps([1, None, None, None, model_hash, None, None, 0, [4]], separators=(",", ":"))

    @classmethod
    def build_model_jspb_header(
        cls,
        model: ModelInfo | str,
        session_uuid: str,
        thinking_flag: int | None = None,
        model_number: int | None = None,
    ) -> str:
        """Synthesizes x-goog-ext-525001261-jspb JSON envelope (17 or 25 elements for Argon4p0)."""
        info = resolve_model(model) if isinstance(model, str) else model
        eff_thinking = thinking_flag if thinking_flag is not None else info.thinking_flag
        eff_model_number = model_number if model_number is not None else info.model_number

        envelope: list[Any] = [
            1,
            None,
            None,
            None,
            info.id,
            None,
            None,
            0,
            [4, 5, 6, 8],
            None,
            None,
            info.capacity_tail,
            None,
            None,
            eff_model_number,
            eff_thinking,
            session_uuid,
        ]
        if getattr(info, "mode_tag", None):
            # Field 25 (0-based index 24) is Qp ("Argon4p0") in _.Erd / _.QCb
            envelope.extend([None, None, None, None, None, None, None, info.mode_tag])
        return json.dumps(envelope, separators=(",", ":"))

    @classmethod
    def build_stream_headers(
        cls,
        session_context: Any,
        model: ModelInfo | str,
        origin_host: str = "gemini.google.com",
    ) -> dict[str, str]:
        """Headers for StreamGenerate endpoint."""
        info = resolve_model(model) if isinstance(model, str) else model
        session_uuid = getattr(session_context, "session_uuid", str(uuid.uuid4()).upper())
        cookies = getattr(session_context, "cookies", {})

        headers = {
            "Host": origin_host,
            "Origin": f"https://{origin_host}",
            "Referer": f"https://{origin_host}/",
            "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
            "User-Agent": cls.DEFAULT_USER_AGENT,
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
            "sec-ch-ua": cls.DEFAULT_SEC_CH_UA,
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": cls.DEFAULT_SEC_CH_UA_PLATFORM,
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
            "x-same-domain": "1",
            "x-goog-ext-525001261-jspb": cls.build_model_jspb_header(info, session_uuid),
            "x-goog-ext-73010989-jspb": "[0]",
            "x-goog-ext-73010990-jspb": "[0,0,0]",
            "x-goog-ext-525005358-jspb": json.dumps([str(uuid.uuid4()).upper(), 1], separators=(",", ":")),
            "Cookie": cls.format_cookies(cookies),
        }
        sapisid = cls.extract_sapisid(cookies)
        if sapisid:
            headers["Authorization"] = cls.make_sapisidhash(sapisid, origin=f"https://{origin_host}")
        return headers

    @classmethod
    def build_batch_headers(
        cls,
        session_context: Any,
        origin_host: str = "gemini.google.com",
    ) -> dict[str, str]:
        """Headers for batchexecute RPC endpoint."""
        cookies = getattr(session_context, "cookies", {})
        headers = {
            "Host": origin_host,
            "Origin": f"https://{origin_host}",
            "Referer": f"https://{origin_host}/",
            "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
            "User-Agent": cls.DEFAULT_USER_AGENT,
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
            "sec-ch-ua": cls.DEFAULT_SEC_CH_UA,
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": cls.DEFAULT_SEC_CH_UA_PLATFORM,
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
            "x-same-domain": "1",
            "Cookie": cls.format_cookies(cookies),
        }
        sapisid = cls.extract_sapisid(cookies)
        if sapisid:
            headers["Authorization"] = cls.make_sapisidhash(sapisid, origin=f"https://{origin_host}")
        return headers

    @classmethod
    def build_upload_headers(
        cls,
        session_context: Any,
        push_host: str = "content-push.googleapis.com",
    ) -> dict[str, str]:
        """Headers for content-push file upload endpoint."""
        cookies = getattr(session_context, "cookies", {})
        push_id = getattr(session_context, "push_id", "feeds/mcudyrk2a4khkz")

        headers = {
            "Host": push_host,
            "Origin": "https://gemini.google.com",
            "Referer": "https://gemini.google.com/",
            "User-Agent": cls.DEFAULT_USER_AGENT,
            "Push-ID": push_id,
            "sec-ch-ua": cls.DEFAULT_SEC_CH_UA,
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": cls.DEFAULT_SEC_CH_UA_PLATFORM,
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "cross-site",
            "Cookie": cls.format_cookies(cookies),
        }
        sapisid = cls.extract_sapisid(cookies)
        if sapisid:
            headers["Authorization"] = cls.make_sapisidhash(sapisid)
        return headers


KNOWN_MODEL_LABELS: set[str] = {
    "3.5 Flash-Lite",
    "3.8 Flash",
    "3.1 Pro",
    "4 Argon",
}


def _find_model_label_in_tree(node: Any, labels: set[str] = KNOWN_MODEL_LABELS) -> str | None:
    if isinstance(node, str):
        return node if node in labels else None
    if isinstance(node, list):
        for child in node:
            found = _find_model_label_in_tree(child, labels)
            if found:
                return found
    return None


def extract_answered_by_label(inner: Any) -> str | None:
    """
    Extracts the picker model label Google stamped on a StreamGenerate reply
    (e.g. '4 Argon', '3.8 Flash', '3.5 Flash-Lite', '3.1 Pro') from candidate metadata.
    Candidate text (index 1) is excluded so model names mentioned in prose are ignored.
    """
    if not isinstance(inner, list):
        return None
    candidate = inner[4][0] if len(inner) > 4 and isinstance(inner[4], list) and inner[4] else None
    if isinstance(candidate, list):
        metadata_parts = [part for idx, part in enumerate(candidate) if idx != 1]
        found = _find_model_label_in_tree(metadata_parts)
        if found:
            return found
    outer_parts = inner[:4] + (inner[5:] if len(inner) > 5 else [])
    return _find_model_label_in_tree(outer_parts)


@dataclass(frozen=True)
class WorkspaceExtension:
    id: str
    name: str
    tag: str
    description: str
    aliases: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "tag": self.tag,
            "description": self.description,
            "aliases": list(self.aliases),
        }


class WorkspaceExtensionRegistry:
    """
    Registry and prompt normalizer for Google Workspace Extensions in Gemini Web
    (@YouTube, @Gmail, @Google Drive, @Google Docs, @Google Flights, @Google Hotels, @Google Maps).
    """

    EXTENSIONS: tuple[WorkspaceExtension, ...] = (
        WorkspaceExtension(
            id="youtube",
            name="YouTube",
            tag="@YouTube",
            description="Search, analyze, and summarize YouTube videos and channels.",
            aliases=("youtube", "@youtube", "yt", "@yt"),
        ),
        WorkspaceExtension(
            id="gmail",
            name="Gmail",
            tag="@Gmail",
            description="Find, read, and summarize emails in Google Workspace Gmail.",
            aliases=("gmail", "@gmail", "google mail", "@google mail"),
        ),
        WorkspaceExtension(
            id="google_drive",
            name="Google Drive",
            tag="@Google Drive",
            description="Search and read files, folders, and PDFs stored in Google Drive.",
            aliases=("google_drive", "google drive", "@google drive", "googledrive", "drive", "@drive"),
        ),
        WorkspaceExtension(
            id="google_docs",
            name="Google Docs",
            tag="@Google Docs",
            description="Find and read documents in Google Docs.",
            aliases=("google_docs", "google docs", "@google docs", "googledocs", "docs", "@docs"),
        ),
        WorkspaceExtension(
            id="google_flights",
            name="Google Flights",
            tag="@Google Flights",
            description="Real-time flight schedules, prices, and travel itinerary planning.",
            aliases=("google_flights", "google flights", "@google flights", "flights", "@flights"),
        ),
        WorkspaceExtension(
            id="google_hotels",
            name="Google Hotels",
            tag="@Google Hotels",
            description="Search and compare hotels, rates, and availability.",
            aliases=("google_hotels", "google hotels", "@google hotels", "hotels", "@hotels"),
        ),
        WorkspaceExtension(
            id="google_maps",
            name="Google Maps",
            tag="@Google Maps",
            description="Places, directions, distances, and local business search via Google Maps.",
            aliases=("google_maps", "google maps", "@google maps", "maps", "@maps"),
        ),
        WorkspaceExtension(
            id="google_workspace",
            name="Google Workspace",
            tag="@Google Workspace",
            description="Unified access across Gmail, Google Drive, and Google Docs.",
            aliases=("google_workspace", "google workspace", "@google workspace", "workspace", "@workspace"),
        ),
    )

    _INLINE_PATTERNS: tuple[tuple[re.Pattern[str], WorkspaceExtension], ...] = (
        (re.compile(r"(?<!\w)@youtube\b", re.IGNORECASE), EXTENSIONS[0]),
        (re.compile(r"(?<!\w)@gmail\b", re.IGNORECASE), EXTENSIONS[1]),
        (re.compile(r"(?<!\w)@(?:google\s+drive|googledrive|drive)\b", re.IGNORECASE), EXTENSIONS[2]),
        (re.compile(r"(?<!\w)@(?:google\s+docs|googledocs|docs)\b", re.IGNORECASE), EXTENSIONS[3]),
        (re.compile(r"(?<!\w)@(?:google\s+flights|flights)\b", re.IGNORECASE), EXTENSIONS[4]),
        (re.compile(r"(?<!\w)@(?:google\s+hotels|hotels)\b", re.IGNORECASE), EXTENSIONS[5]),
        (re.compile(r"(?<!\w)@(?:google\s+maps|maps)\b", re.IGNORECASE), EXTENSIONS[6]),
        (re.compile(r"(?<!\w)@(?:google\s+workspace|workspace)\b", re.IGNORECASE), EXTENSIONS[7]),
    )

    @classmethod
    def list_all(cls) -> list[dict[str, Any]]:
        """Returns all registered Workspace extensions as dictionaries."""
        return [ext.to_dict() for ext in cls.EXTENSIONS]

    @classmethod
    def resolve(cls, key: str) -> WorkspaceExtension | None:
        """Resolves an extension ID, name, or @-tag to its canonical WorkspaceExtension."""
        if not key or not isinstance(key, str):
            return None
        norm = key.strip().lower()
        for ext in cls.EXTENSIONS:
            if norm in (ext.id.lower(), ext.name.lower(), ext.tag.lower()) or norm in ext.aliases:
                return ext
        return None

    @classmethod
    def normalize_prompt_and_extensions(
        cls,
        prompt: str,
        extensions: list[str] | None = None,
    ) -> tuple[str, list[str]]:
        """
        Normalizes inline @-mentions (e.g. `@Drive` -> `@Google Drive`, `@docs` -> `@Google Docs`)
        and prepends any explicitly requested `extensions` as canonical `@Tag ` prefixes
        expected by Google Gemini Web StreamGenerate.
        Returns `(normalized_prompt, active_extension_ids)`.
        """
        text = prompt or ""
        active_ids: list[str] = []

        # 1. Normalize inline @-tags in text
        for pattern, ext in cls._INLINE_PATTERNS:
            if pattern.search(text):
                text = pattern.sub(ext.tag, text)
                if ext.id not in active_ids:
                    active_ids.append(ext.id)

        # 2. Apply explicit extensions list
        prefix_tags: list[str] = []
        if extensions:
            for item in extensions:
                ext_obj = cls.resolve(item)
                if ext_obj:
                    if ext_obj.id not in active_ids:
                        active_ids.append(ext_obj.id)
                    if ext_obj.tag not in text and ext_obj.tag not in prefix_tags:
                        prefix_tags.append(ext_obj.tag)

        if prefix_tags:
            prefix_str = " ".join(prefix_tags)
            text = f"{prefix_str} {text}".strip() if text else prefix_str

        return text, active_ids

